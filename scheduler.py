import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from pipeline import run_pipeline
from email_digest import send_digest
from models import init_db
import random

# NOTE: this file is NOT what runs the weekly digest in production. The
# BlockingScheduler set up below (including its `send_digest` cron job) is
# for local/manual use only and is not part of the deployed Procfile — it
# has to be started as a standalone long-running process, which nothing on
# Railway does. In production, the weekly digest runs via a dedicated
# Railway Cron Job service ("weekly-digest-cron") that executes
# `python email_digest.py` directly on the schedule "0 8 * * 1" (see
# email_digest.py). If you're trying to change when/how the real weekly
# digest fires, change that Cron Job's schedule/command, not this file.

# Ensure the schema (including searches.user_id) exists before any pipeline run.
init_db()

# Set up logging so we can see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("pipeline.log"),
        logging.StreamHandler()
    ]
)

# A list of artists to rotate through automatically
ARTISTS = [
    "Radiohead", "Kendrick Lamar", "Amy Winehouse", "The Weeknd",
    "Frank Ocean", "Billie Eilish", "Tyler the Creator", "SZA",
    "Arctic Monkeys", "Childish Gambino", "Portishead", "James Blake"
]

def scheduled_job():
    """Pick a random artist and run the pipeline"""
    artist = random.choice(ARTISTS)
    logging.info(f"Scheduled run starting for: {artist}")
    try:
        insight = run_pipeline(artist)
        logging.info(f"Scheduled run completed successfully for: {artist}")
    except Exception as e:
        logging.error(f"Pipeline failed for {artist}: {e}")

# Set up the scheduler
scheduler = BlockingScheduler()

# Run once immediately, then every day at 9am
scheduled_job()  # run right now so you can see it working
scheduler.add_job(scheduled_job, 'cron', hour=9, minute=0)

# Weekly email digest — every Monday at 8am
scheduler.add_job(send_digest, 'cron', day_of_week='mon', hour=8, minute=0)

print("\n⏰ Scheduler running — pipeline will execute daily at 9am")
print("Press Ctrl+C to stop\n")

try:
    scheduler.start()
except KeyboardInterrupt:
    print("\nScheduler stopped.")