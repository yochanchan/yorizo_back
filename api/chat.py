import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
from app.schemas.chat import ChatTurnRequest, ChatTurnResponse
from database import get_db
from models import CompanyProfile, Conversation, Document, Memory, Message, User
from services import rag as rag_service  # RAG 用サービスレイヤー

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
    """?????????????????????"""
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
                    lines.append(f"??: {question}")
            except Exception:
                lines.append(f"Yorizo: {msg.content}")
        else:
            lines.append(f"????: {msg.content}")
    return "\n".join(lines)


def _collect_structured_context(db: Session, user: Optional[User], conversation: Conversation) -> List[str]:
    """
    /company, /memory, /documents に相当する情報を日本語テキストに整形する。
    """
    del conversation  # 将来的に会話単位の要素を扱う余地を残す
    pieces: List[str] = []
    if not user:
        return pieces

    profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == user.id).first()
    if profile:
        pieces.append(
            "【会社情報】\n"
            f"会社名: {profile.company_name or '未登録'}\n"
            f"業種: {profile.industry or '未登録'}\n"
            f"従業員数: {profile.employees_range or '未登録'}\n"
            f"年商レンジ: {profile.annual_sales_range or '未登録'}\n"
            f"所在地: {profile.location_prefecture or '未登録'}\n"
        )

    memory = (
        db.query(Memory)
        .filter(Memory.user_id == user.id)
        .order_by(Memory.last_updated_at.desc())
        .first()
    )
    if memory:
        lines = ["【Yorizoの記憶】"]
        if memory.current_concerns:
            lines.append(f"- 現在気になっていること: {memory.current_concerns}")
        if memory.important_points:
            lines.append(f"- 専門家に伝えたいポイント: {memory.important_points}")
        if memory.remembered_facts:
            lines.append(f"- 最近のメモ: {memory.remembered_facts}")
        pieces.append("\n".join(lines))

    docs = (
        db.query(Document)
        .filter(Document.user_id == user.id)
        .order_by(Document.uploaded_at.desc())
        .limit(5)
        .all()
    )
    if docs:
        lines = ["【アップロード資料（直近）】"]
        for doc in docs:
            meta_parts: List[str] = []
            if doc.doc_type:
                meta_parts.append(doc.doc_type)
            if doc.period_label:
                meta_parts.append(doc.period_label)
            meta = " / ".join(meta_parts)
            title = getattr(doc, "title", None) or doc.filename or "無題"
            lines.append(f"- {title}{f'（{meta}）' if meta else ''}")
        pieces.append("\n".join(lines))

    return pieces




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

    # selection / free text 判定
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

    # 旧フィールドのフォールバック
    if not selection:
        choice_id = payload.selected_option_id
        free_text = payload.message

    if not free_text and not choice_label and not choice_id:
        raise HTTPException(status_code=400, detail="入力が空です。メッセージまたは選択肢を送信してください。")

    option_label = choice_label or (choice_id and _find_option_label(history, choice_id))
    display_text = free_text or option_label or choice_id or ""

    user_entries: List[str] = []
    if choice_id:
        # choice_id は内部管理用。先頭にだけメモとして残す。
        user_entries.append(f"[choice_id:{choice_id}] {display_text}")
    else:
        user_entries.append(display_text.strip())

    for text in user_entries:
        saved = _persist_message(db, conversation, "user", text)
        history.append(saved)

    # 会話のメインテーマ（main_concern）がまだなければ最初の入力を保存
    if not conversation.main_concern and user_entries:
        conversation.main_concern = user_entries[0][:255]

    # LLM に渡すクエリテキスト
    query_text = (
        free_text
        or option_label
        or conversation.main_concern
        or (payload.category or "????????")
    )

    try:
        rag_chunks = await rag_service.retrieve_context(
            db=db,
            user_id=user.id if user else None,
            company_id=None,
            query=query_text,
            top_k=8,
        )
    except Exception:
        logger.exception("failed to retrieve RAG context")
        rag_chunks = []

    structured_chunks = _collect_structured_context(db, user, conversation)

    all_chunks: List[str] = []
    if rag_chunks:
        all_chunks.extend(rag_chunks)
    if structured_chunks:
        all_chunks.extend(structured_chunks)

    if all_chunks:
        context_text = "\n\n".join(all_chunks)
    else:
        context_text = (
            "?????????????????????????????????????????"
            "?????????????????????????????"
        )

    history_text = _history_as_text(history)


    user_prompt_text = (
        "以下は、この会社や似た事業者に関する過去の相談メモ・チャット・資料の抜粋です。"
        "これらを参考にしながら、ユーザーの現在の質問に日本語で回答してください。\n\n"
        "# コンテキスト\n"
        f"{context_text}\n\n"
        "# これまでの会話の流れ\n"
        f"{history_text}\n\n"
        "# ユーザーの質問\n"
        f"{query_text}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt_text},
    ]

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

    # 会話ステップ・ステータス更新
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

    # アシスタント側メッセージを保存（JSON そのまま）
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
    従来のエントリポイント。ガイド付きフローにフォワードする。
    """
    return await _run_guided_chat(payload, db)
