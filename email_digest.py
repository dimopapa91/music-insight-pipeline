"""
email_digest.py — Weekly music insight email digest
Sends a summary of the week's top artists + Claude insights via Gmail SMTP.

Setup: add these to your .env file:
  DIGEST_EMAIL_FROM=dimpapa91@gmail.com
  DIGEST_EMAIL_TO=dimpapa91@gmail.com
  GMAIL_APP_PASSWORD=your_gmail_app_password  (Gmail > Security > App Passwords)
"""

import os
import json
import smtplib
import logging
import psycopg2
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)


def get_db_connection():
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))


def get_weekly_data():
    """Fetch this week's searches from the DB"""
    conn = get_db_connection()
    cur = conn.cursor()
    one_week_ago = datetime.now() - timedelta(days=7)
    cur.execute("""
        SELECT * FROM (
            SELECT DISTINCT ON (artist_name)
                artist_name, claude_insight, searched_at, top_tracks
            FROM searches
            WHERE searched_at >= %s
            ORDER BY artist_name, searched_at DESC
        ) sub
        ORDER BY searched_at DESC
        LIMIT 8
    """, (one_week_ago,))
    rows = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM searches WHERE searched_at >= %s", (one_week_ago,))
    total_this_week = cur.fetchone()[0]

    cur.close()
    conn.close()
    return rows, total_this_week


def build_html_email(rows, total_this_week):
    """Build styled HTML email"""
    date_str = datetime.now().strftime("%d %B %Y")

    artist_blocks = ""
    for artist_name, insight, searched_at, top_tracks_raw in rows:
        tracks = top_tracks_raw if isinstance(top_tracks_raw, list) else json.loads(top_tracks_raw)
        track_list = "  ·  ".join(t["name"] for t in tracks[:4])
        short_insight = insight[:280] + "…" if len(insight) > 280 else insight

        artist_blocks += f"""
        <div style="background:#fff;border:1px solid #e8e8e8;padding:20px 24px;margin-bottom:16px;">
            <div style="font-family:'Courier New',monospace;font-size:14px;font-weight:700;color:#111;margin-bottom:4px;">{artist_name}</div>
            <div style="font-family:'Courier New',monospace;font-size:11px;color:#1da0c3;margin-bottom:10px;">{track_list}</div>
            <div style="font-family:Georgia,serif;font-size:13px;color:#444;line-height:1.7;">{short_insight}</div>
        </div>
        """

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f5f3;font-family:'Courier New',monospace;">
    <div style="max-width:580px;margin:32px auto;">

        <!-- Header -->
        <div style="background:#111;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;">
            <span style="font-size:16px;font-weight:700;letter-spacing:3px;color:#1da0c3;">MIP</span>
            <span style="font-size:11px;color:#666;">Weekly Digest · {date_str}</span>
        </div>

        <!-- Intro -->
        <div style="background:#fff;border:1px solid #e8e8e8;border-top:none;padding:20px 24px 16px;">
            <p style="font-size:13px;color:#333;margin:0;">
                Here's what your Music Insight Pipeline found this week.
                <strong>{len(rows)} artists</strong> analysed across <strong>{total_this_week} searches</strong>.
            </p>
        </div>

        <!-- Artists -->
        <div style="padding:16px 0;">
            <div style="font-size:10px;color:#1da0c3;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px;">This Week's Artists</div>
            {artist_blocks}
        </div>

        <!-- Footer -->
        <div style="border-top:1px solid #ddd;padding:16px 0;text-align:center;">
            <p style="font-size:10px;color:#aaa;margin:0;">Music Insight Pipeline · Auto-generated · <a href="http://localhost:5000" style="color:#1da0c3;">Open Dashboard</a></p>
        </div>
    </div>
</body>
</html>
"""
    return html


def build_text_email(rows, total_this_week):
    """Plain text fallback"""
    lines = [
        f"MUSIC INSIGHT PIPELINE — Weekly Digest",
        f"Week ending {datetime.now().strftime('%d %B %Y')}",
        f"{len(rows)} artists | {total_this_week} total searches this week",
        "=" * 50,
        ""
    ]
    for artist_name, insight, searched_at, top_tracks_raw in rows:
        tracks = top_tracks_raw if isinstance(top_tracks_raw, list) else json.loads(top_tracks_raw)
        track_list = ", ".join(t["name"] for t in tracks[:4])
        lines.append(f"▶ {artist_name}")
        lines.append(f"  Tracks: {track_list}")
        lines.append(f"  {insight[:200]}…")
        lines.append("")
    return "\n".join(lines)


def send_digest():
    """Build and send the weekly digest email"""
    from_email = os.getenv("DIGEST_EMAIL_FROM")
    to_email = os.getenv("DIGEST_EMAIL_TO")
    app_password = os.getenv("GMAIL_APP_PASSWORD")

    if not all([from_email, to_email, app_password]):
        logging.warning("Email digest skipped — DIGEST_EMAIL_FROM, DIGEST_EMAIL_TO or GMAIL_APP_PASSWORD not set in .env")
        return

    rows, total_this_week = get_weekly_data()
    if not rows:
        logging.info("No searches this week — skipping digest.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎵 Your Weekly Music Digest — {datetime.now().strftime('%d %b %Y')}"
    msg["From"] = from_email
    msg["To"] = to_email

    msg.attach(MIMEText(build_text_email(rows, total_this_week), "plain"))
    msg.attach(MIMEText(build_html_email(rows, total_this_week), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_email, app_password)
            server.sendmail(from_email, to_email, msg.as_string())
        logging.info(f"Weekly digest sent to {to_email}")
    except smtplib.SMTPAuthenticationError:
        logging.error("Gmail authentication failed. Check GMAIL_APP_PASSWORD in .env — it must be a Gmail App Password, not your regular password.")
    except Exception as e:
        logging.error(f"Failed to send digest: {e}")


if __name__ == "__main__":
    send_digest()
