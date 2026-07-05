"""main.py — command-line entry point for the Waveline pipeline.

Prompts for an artist name and runs the full ETL pipeline
(fetch top tracks → Claude analysis → save to PostgreSQL).

For the web app run ``dashboard.py``; for scheduled runs, ``scheduler.py``.
All pipeline logic lives in ``pipeline.py`` — this file is just a thin CLI.
"""

from pipeline import run_pipeline


def main():
    artist = input("Enter an artist name: ").strip()
    if not artist:
        print("No artist entered.")
        return
    print(f"\nRunning pipeline for {artist}...\n")
    insight = run_pipeline(artist)
    print(insight)


if __name__ == "__main__":
    main()
