import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
from app.schemas.chat import ChatTurnRequest, ChatTurnResponse
from database import get_db
from models import Conversation, Document, Message, User

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
あなたは日本語で回答する経営相談AI「Yorizo」です。
- 1回の応答では短い状況整理と次に聞きたい質問を1つだけ返してください。
- 「question」にはユーザーに投げかける短い質問だけを書いてください（1〜2文）。長い「状況の整理」「診断フロー（概要）」などは question に入れないでください。
- 選択肢は3〜5個の短い日本語ラベルを提示します。id は英語の内部キーですが、label/value は日本語で書きます。id をユーザー向けテキストに含めないでください。
- 全体で4〜5ステップ程度で完結させ、5問目では done=true を返してください。
- 返却は必ず次のJSONのみ。余計なテキストは出力しないでください。
{
  "reply": string,
  "question": string,
  "options": [
    { "id": "string", "label": "表示ラベル", "value": "保存用の値" }
  ],
  "allow_free_text": true,
  "step": number,
  "done": boolean
}
""".strip()


def _ensure_user(db: Session, user_id: Optional[str]) -> Optional[User]:
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        return user
    user = User(id=user_id, nickname="ゲスト")
    db.add(user)
    db.commit()
    return user


def _get_or_create_conversation(
    db: Session, conversation_id: Optional[str], user: Optional[User], category: Optional[str]
) -> Conversation:
    if conversation_id:
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv:
            if category and not conv.category:
                conv.category = category
                db.add(conv)
                db.commit()
            return conv
    conv = Conversation(
        user_id=user.id if user else None,
        started_at=datetime.utcnow(),
        channel="chat",
        category=category,
        status="in_progress",
        step=0,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _persist_message(db: Session, conversation: Conversation, role: str, content: str) -> Message:
    msg = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        created_at=datetime.utcnow(),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _find_option_label(messages: List[Message], option_id: str) -> Optional[str]:
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        try:
            data = json.loads(msg.content)
            for opt in data.get("options") or []:
                if isinstance(opt, dict) and opt.get("id") == option_id:
                    return opt.get("label") or opt.get("value")
        except Exception:
            continue
    return None


def _history_as_text(messages: List[Message]) -> str:
    lines: List[str] = []
    for msg in messages[-10:]:
        if msg.role == "assistant":
            try:
                data = json.loads(msg.content)
                reply = data.get("reply") or data.get("message")
                question = data.get("question")
                if reply:
                    lines.append(f"Yorizo: {reply}")
                if question:
                    lines.append(f"質問: {question}")
            except Exception:
                lines.append(f"Yorizo: {msg.content}")
        else:
            lines.append(f"ユーザー: {msg.content}")
    return "\n".join(lines)


async def _reference_bullets(db: Session, conversation: Conversation, user: Optional[User], query_text: str) -> str:
    docs_query = db.query(Document).filter(Document.ingested.is_(True))
    if conversation.id:
        docs_query = docs_query.filter(
            or_(Document.conversation_id == conversation.id, Document.user_id == conversation.user_id)
        )
    if user and user.id:
        docs_query = docs_query.filter(or_(Document.user_id == user.id, Document.company_id == user.id))
    docs = docs_query.order_by(Document.uploaded_at.desc()).limit(30).all()

    if not docs:
        return ""

    collections: set[str] = set()
    for doc in docs:
        if doc.company_id:
            collections.add(f"company-{doc.company_id}")
        else:
            collections.add("global")

    snippets: List[str] = []
    for collection in collections:
        try:
            from app.rag.store import similarity_search  # local import to avoid circular

            hits = await similarity_search(collection, query_text or "経営", k=3)
            for hit in hits:
                text = str(hit.get("text") or "").replace("\n", " ").strip()
                if text:
                    snippets.append(text[:240])
        except Exception:
            continue
    seen: set[str] = set()
    bullets: List[str] = []
    for text in snippets:
        if text in seen:
            continue
        seen.add(text)
        bullets.append(f"- {text}")
        if len(bullets) >= 5:
            break
    return "\n".join(bullets)


async def _run_guided_chat(payload: ChatTurnRequest, db: Session) -> ChatTurnResponse:
    if not payload.message and not payload.selected_option_id and not payload.selection:
        raise HTTPException(status_code=400, detail="メッセージまたは選択肢を送信してください。")

    user = _ensure_user(db, payload.user_id or "demo-user")
    conversation = _get_or_create_conversation(db, payload.conversation_id, user, payload.category)

    history: List[Message] = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .all()
    )

    # Determine selection/free text
    selection = payload.selection
    choice_id = None
    choice_label = None
    free_text = None

    if selection:
        if selection.type == "choice":
            choice_id = selection.id
            choice_label = selection.label
        elif selection.type == "free_text":
            free_text = selection.text or payload.message

    # Legacy fallback
    if not selection:
        choice_id = payload.selected_option_id
        free_text = payload.message

    if not free_text and not choice_label and not choice_id:
        raise HTTPException(status_code=400, detail="入力が空です。メッセージまたは選択肢を送信してください。")

    option_label = choice_label or (choice_id and _find_option_label(history, choice_id))
    display_text = free_text or option_label or choice_id or ""

    user_entries: List[str] = []
    if choice_id:
        user_entries.append(f"[choice_id:{choice_id}] {display_text}")
    else:
        user_entries.append(display_text.strip())

    for text in user_entries:
        saved = _persist_message(db, conversation, "user", text)
        history.append(saved)

    if not conversation.main_concern and user_entries:
        conversation.main_concern = user_entries[0][:255]

    query_text = free_text or option_label or conversation.main_concern or (payload.category or "経営相談")
    reference_block = await _reference_bullets(db, conversation, user, query_text)
    history_text = _history_as_text(history)

    user_prompt_text = (
        "これまでの会話を踏まえて、次の1問だけを考えてください。\n"
        "- 4〜5ステップで終わらせ、5問目では done=true にする。\n"
        "- 日本語のみ。options.id は内部キーで、ユーザー向けテキストには含めない。\n"
        "- 質問は1つだけ、1〜2文で短く。選択肢は3〜5個の短い日本語ラベル。\n"
        "- 深掘りしすぎず、モヤモヤをざっくり構造化する。\n\n"
        f"会話の流れ:\n{history_text}"
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if reference_block:
        messages.append({"role": "system", "content": f"参考情報:\n{reference_block}"})
    messages.append({"role": "user", "content": user_prompt_text})

    try:
        raw_json = chat_completion_json(messages)
        raw = json.loads(raw_json or "{}")
        raw.setdefault("options", [])
        raw.setdefault("allow_free_text", True)
        current_step_value = conversation.step or 0
        try:
            current_step_int = int(current_step_value)
        except (TypeError, ValueError):
            current_step_int = 0
        try:
            provided_step = int(raw.get("step")) if raw.get("step") is not None else None
        except (TypeError, ValueError):
            provided_step = None
        raw["step"] = provided_step if provided_step is not None else current_step_int + 1
        raw.setdefault("done", False)
        if not raw["done"] and raw["step"] >= 5:
            raw["done"] = True
        result = ChatTurnResponse(conversation_id=conversation.id, **raw)
    except AzureNotConfiguredError:
        logger.exception("Azure OpenAI is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="YorizoのAI設定がまだ完了していません。しばらく時間をおいてから再度お試しください。",
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("guided chat generation failed")
        raise HTTPException(
            status_code=500,
            detail="Yorizoとの通信中にエラーが発生しました。時間をおいてもう一度お試しください。",
        ) from exc

    prior_step_value = conversation.step or 0
    try:
        prior_step_int = int(prior_step_value)
    except (TypeError, ValueError):
        prior_step_int = 0
    conversation.step = (result.step if isinstance(result.step, int) else None) or prior_step_int + 1
    conversation.status = "completed" if result.done else "in_progress"
    if result.done:
        conversation.ended_at = datetime.utcnow()
    db.add(conversation)
    db.commit()

    assistant_payload = result.model_dump()
    assistant_payload["conversation_id"] = conversation.id
    _persist_message(db, conversation, "assistant", json.dumps(assistant_payload, ensure_ascii=False))

    return ChatTurnResponse(
        conversation_id=conversation.id,
        reply=result.reply,
        question=result.question,
        options=result.options,
        allow_free_text=result.allow_free_text,
        step=result.step,
        done=result.done,
    )


@router.post("/guided", response_model=ChatTurnResponse)
async def guided_chat_turn(payload: ChatTurnRequest, db: Session = Depends(get_db)) -> ChatTurnResponse:
    return await _run_guided_chat(payload, db)


@router.post("", response_model=ChatTurnResponse)
async def chat_turn(payload: ChatTurnRequest, db: Session = Depends(get_db)) -> ChatTurnResponse:
    """
    Backward-compatible entry that forwards to the guided flow.
    """
    return await _run_guided_chat(payload, db)
