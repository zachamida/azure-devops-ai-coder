import asyncio
import logging
import sys

from fastapi import FastAPI

from .webhook import router as webhook_router
from .queue_worker import start_queue_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
# Suppress Azure SDK verbose HTTP logging
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _monitored_consumer():
    """Wrapper that logs any unhandled exception from the queue consumer."""
    try:
        await start_queue_consumer()
    except Exception:
        logger.exception("Queue consumer crashed")


async def lifespan(app: FastAPI):
    consumer_task = asyncio.create_task(_monitored_consumer())
    logger.info("Queue consumer started")
    yield
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="AI Coder",
    description="Azure DevOps Work Item to Code Implementation",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)
