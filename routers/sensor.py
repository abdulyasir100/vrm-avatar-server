"""POST /sensor — Receive health/usage data from Companion Sensor app."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from services import sensor as sensor_service

logger = logging.getLogger(__name__)
router = APIRouter()


class SensorRequest(BaseModel):
    screen_time: dict[str, int] = {}
    steps: int = 0
    timestamp: str = ""


class SensorResponse(BaseModel):
    ok: bool


@router.post("/sensor", response_model=SensorResponse)
async def post_sensor(req: SensorRequest):
    """Receive periodic sensor data from Companion Sensor Android app."""
    data = {"steps": req.steps}  # always include steps (even 0) for step goal check
    if req.screen_time:
        data["screen_time"] = req.screen_time

    sensor_service.store(data, req.timestamp)

    total_screen = sum(req.screen_time.values())
    logger.info(f"[sensor] steps={req.steps}, screen_time={total_screen}min, apps={list(req.screen_time.keys())}")
    return SensorResponse(ok=True)


@router.get("/sensor/step-goal")
async def get_step_goal():
    return {"step_goal": sensor_service.get_step_goal()}


class StepGoalRequest(BaseModel):
    goal: int


@router.post("/sensor/step-goal", response_model=SensorResponse)
async def set_step_goal(req: StepGoalRequest):
    sensor_service.set_step_goal(req.goal)
    logger.info(f"[sensor] Step goal updated to {req.goal}")
    return SensorResponse(ok=True)
