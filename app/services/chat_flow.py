from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.openai_client import chat_json_safe
from app.models import CompanyProfile, Conversation, Document, Memory, Message, User
from app.models.enums import ConversationStatus
from app.schemas.chat import ChatTurnRequest, ChatTurnResponse
from app.services import rag as rag_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
あなたは共感的な経営相談AI「Yorizo」です。
中小企業の経営者の悩みを整理し、「今できる一歩」に絞って対話します。やわらかい丁寧語で話し、否定・説教・タメ口・上から目線は避け、「現実的だけれど前向き」なトーンを保ってください。

【出力形式（最重要）】
・必ず JSON オブジェクトを 1 つだけ返します。
・前後に説明文やマークダウン、コードブロック（```）などは一切書きません。
・トップレベルのキーは次の 5 つだけにします:
  "reply", "question", "options", "allow_free_text", "done"
・すべてのキーと文字列はダブルクォートで囲み、末尾カンマは禁止、true/false は小文字の JSON boolean にします。

【各フィールドの仕様】

1. "reply": string
・日本語 200 文字以内・最大 2 段落（各 2〜3 文）。
・「共感 → 状況の簡単な整理 → 今できる具体的な一歩」を短く伝えます。
・ユーザーが答えやすいよう、複数の選択肢を提案する形で、あとで出す "question" の内容と自然につながるように書きます。

2. "question": string
・日本語 30 文字以内の質問文 1 文。
・"reply" で示した一歩に関する、ごく答えやすい確認または提案ベースの質問にします。
・説明文や箇条書きは入れません。

3. "options": array
・3~4 件の配列。
・各要素は次の 3 キーを持つオブジェクトです（すべて string）:
  - "id": 英小文字とアンダースコアのみの識別子（例: "check_cash_flow"）。
  - "label": 日本語 15 文字以内。"question" への回答や次の一歩を少し具体化した文にします。
  - "value": 日本語 15 文字以内。"question" への回答や次の一歩を少し具体化した文にします。"label" と同一出力。
・"question" に対応する、ユーザーがすぐに選べる選択肢だけを並べます。

4. "allow_free_text": boolean
・自由入力を許可するか。基本は true を返します。

5. "done": boolean
・会話をここで締めたいとき true。基本は false。

【スタイル】
・相手の感情に寄り添いながら、「いま分かっている事実」と「今日からできる小さな一歩」を優先して伝えます。
・分からない数字や事実は新しく作らず、「まだ分かりません」などと率直に伝えます。

この仕様どおりの JSON オブジェクトだけを出力してください。
""".strip()

FALLBACK_REPLY = "Yorizo が考えるのに失敗しました。管理者にお問い合わせください。"


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
        status=ConversationStatus.IN_PROGRESS.value,
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
    """直近の会話を読みやすいテキストに整形する。"""
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


def _collect_structured_context(db: Session, user: Optional[User], conversation: Conversation) -> List[str]:
    """
    /company, /memory, /documents の情報を日本語テキストに整形して返す。
    """
    del conversation  # 将来の拡張余地
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
        lines = ["【アップロードされた資料（直近）】"]
        for doc in docs:
            meta_parts: List[str] = []
            if doc.doc_type:
                meta_parts.append(doc.doc_type)
            if doc.period_label:
                meta_parts.append(doc.period_label)
            meta = " / ".join(meta_parts) if meta_parts else ""
            title = getattr(doc, "title", None) or doc.filename or "無題"
            suffix = f"（{meta}）" if meta else ""
            lines.append(f"- {title}{suffix}")
        pieces.append("\n".join(lines))

    return pieces


def _build_fallback_response(conversation: Conversation) -> ChatTurnResponse:
    """LLM 失敗時のフォールバックレスポンスを生成する。"""
    current_step_value = conversation.step or 0
    try:
        current_step_int = int(current_step_value)
    except (TypeError, ValueError):
        current_step_int = 0

    return ChatTurnResponse(
        conversation_id=conversation.id,
        reply=FALLBACK_REPLY,
        question="",
        options=[],
        allow_free_text=True,
        step=current_step_int,
        done=False,
    )


async def run_guided_chat(payload: ChatTurnRequest, db: Session) -> ChatTurnResponse:
    if not payload.message and not payload.selected_option_id and not payload.selection:
        raise HTTPException(status_code=400, detail="メッセージまたは選択肢を送信してください")

    user = _ensure_user(db, payload.user_id or "demo-user")
    conversation = _get_or_create_conversation(db, payload.conversation_id, user, payload.category)

    history: List[Message] = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .all()
    )

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

    if not selection:
        choice_id = payload.selected_option_id
        free_text = payload.message

    if not free_text and not choice_label and not choice_id:
        raise HTTPException(status_code=400, detail="入力が空です。メッセージまたは選択肢を送信してください")

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

    query_text = free_text or option_label or conversation.main_concern or (payload.category or "経営に関する相談")

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
            "会社情報や記録はまだ十分に登録されていません。それでもユーザーの入力内容をもとに、"
            "中小企業の経営相談として丁寧にヒアリングを進めてください。"
        )

    history_text = _history_as_text(history)
    user_prompt_text = (
        "以下は、この会社に関する過去の相談メモ・チャット・資料の抜粋です。\n"
        "これらを参照しながら、ユーザーの現在の質問に日本語で回答してください。\n\n"
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

    prior_step_value = conversation.step or 0
    try:
        prior_step_int = int(prior_step_value)
    except (TypeError, ValueError):
        prior_step_int = 0
    next_step = min(prior_step_int + 1, 5)

    used_fallback = False

    llm_result = await chat_json_safe("LLM-CHAT-01-v1", messages)
    if not llm_result.ok or not isinstance(llm_result.value, dict):
        logger.warning("guided chat: LLM failed (%s)", llm_result.error)
        result = _build_fallback_response(conversation)
        used_fallback = True
    else:
        raw = dict(llm_result.value)
        raw.pop("conversation_id", None)
        raw.pop("step", None)
        raw.setdefault("options", [])
        raw.setdefault("allow_free_text", True)
        done_flag = bool(raw.pop("done", False))
        if next_step >= 5:
            done_flag = True

        result = ChatTurnResponse(
            conversation_id=conversation.id,
            step=next_step,
            done=done_flag,
            **raw,
        )

    conversation.step = min(prior_step_int, 5) if used_fallback else next_step
    conversation.status = (
        ConversationStatus.COMPLETED.value if result.done else ConversationStatus.IN_PROGRESS.value
    )
    if result.done:
        conversation.ended_at = datetime.utcnow()
    db.add(conversation)
    db.commit()

    if not used_fallback:
        assistant_payload = result.model_dump()
        assistant_payload["conversation_id"] = conversation.id
        _persist_message(db, conversation, "assistant", json.dumps(assistant_payload, ensure_ascii=False))

    return result
