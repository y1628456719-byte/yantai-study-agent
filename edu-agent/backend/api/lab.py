"""兼容原契约的实验室端点；与 /api/action 共用持久化和幂等服务。"""
from fastapi import APIRouter

from ..core import response
from ..core.errors import invalid_argument
from ..core.schemas import LabActionIn, LabAnswerIn, LabStartIn
from ..services import lab_service, session_store

router = APIRouter(tags=["lab"])


@router.post("/sessions/{session_id}/lab/start")
async def lab_start(session_id: str, payload: LabStartIn):
    session = session_store.snapshot(session_id)
    mode = (payload.mode or "").strip().lower()
    if mode not in ("", "material", "self_assessment"):
        raise invalid_argument("mode 只能是 material 或 self_assessment")
    material_id = payload.material_id
    diagnosis_mode = "material" if material_id or mode == "material" else "self_assessment"
    if diagnosis_mode == "material" and not material_id:
        material_id = ((session.get("learning_state") or {}).get("lab") or {}).get("material_id")
    if diagnosis_mode == "material" and not material_id:
        raise invalid_argument("material 模式必须提供 material_id")
    result = await lab_service.execute(session_id, "start", {}, request_id=payload.request_id,
        expected_revision=payload.expected_revision, material_id=material_id,
        topic=payload.topic, diagnosis_mode=diagnosis_mode)
    return response.ok(result["contract"])


@router.post("/sessions/{session_id}/lab/answer")
async def lab_answer(session_id: str, payload: LabAnswerIn):
    result = await lab_service.execute(session_id, "submit", {
        "question_id": payload.question_id, "answer": payload.answer},
        request_id=payload.request_id, expected_revision=payload.expected_revision)
    return response.ok(result["contract"])


@router.post("/sessions/{session_id}/lab/action")
async def lab_action(session_id: str, payload: LabActionIn):
    action = (payload.action or "").strip().lower()
    if action not in lab_service.PARAMS:
        raise invalid_argument("action 只能是 start / submit / feedback / time / continue / stop")
    params = {key: getattr(payload, key) for key in lab_service.PARAMS[action]}
    if any(value is None for value in params.values()):
        raise invalid_argument("动作缺少必要参数")
    result = await lab_service.execute(session_id, action, params,
        request_id=payload.request_id, expected_revision=payload.expected_revision)
    return response.ok(result["contract"])
