import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from pipeline import run_pipeline
import random

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

print("\n⏰ Scheduler running — pipeline will execute daily at 9am")
print("Press Ctrl+C to stop\n")

try:
    scheduler.start()
except KeyboardInterrupt:
    print("\nScheduler stopped.")