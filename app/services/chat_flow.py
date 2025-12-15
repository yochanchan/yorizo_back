from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional, cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.knowledge_search_agent import search_knowledge
from app.core.openai_client import AzureNotConfiguredError, ChatMessage, chat_json_safe
from app.models import CompanyProfile, Conversation, Document, Memory, Message, User
from app.models.enums import ConversationStatus
from app.schemas.chat import ChatTurnRequest, ChatTurnResponse, Citation
from app.services import rag as rag_service
from app.services.example_answer import build_examples_answer
from app.schemas.chat import ChatMessageInput

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
・ユーザーが言っていない前提は置かず、決めつけの語尾（〜ですよね 等）は避けます。根拠が不足する場合は「不足しているので確認したい」など条件付きで述べます。

この仕様どおりの JSON オブジェクトだけを出力してください。
""".strip()

FALLBACK_REPLY = "Yorizo が考えるのに失敗しました。管理者にお問い合わせください。"
CASE_KEYWORDS = ["事例", "成功例", "参考例", "ケース", "取り組み"]

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
    for msg in messages[-5:]:
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

    user_id = cast(str, user.id)

    profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).first()
    if profile:
        company_name = cast(Optional[str], profile.company_name)
        industry = cast(Optional[str], profile.industry)
        employees_range = cast(Optional[str], profile.employees_range)
        annual_sales_range = cast(Optional[str], profile.annual_sales_range)
        location_prefecture = cast(Optional[str], profile.location_prefecture)
        pieces.append(
            "【会社情報】\n"
            f"会社名: {company_name or '未登録'}\n"
            f"業種: {industry or '未登録'}\n"
            f"従業員数: {employees_range or '未登録'}\n"
            f"年商レンジ: {annual_sales_range or '未登録'}\n"
            f"所在地: {location_prefecture or '未登録'}\n"
        )

    memory = (
        db.query(Memory)
        .filter(Memory.user_id == user_id)
        .order_by(Memory.last_updated_at.desc())
        .first()
    )
    if memory:
        current_concerns = cast(Optional[str], memory.current_concerns)
        important_points = cast(Optional[str], memory.important_points)
        remembered_facts = cast(Optional[str], memory.remembered_facts)
        lines = ["【Yorizoの記憶】"]
        if current_concerns:
            lines.append(f"- 現在気になっていること: {current_concerns}")
        if important_points:
            lines.append(f"- 専門家に伝えたいポイント: {important_points}")
        if remembered_facts:
            lines.append(f"- 最近のメモ: {remembered_facts}")
        pieces.append("\n".join(lines))

    docs = (
        db.query(Document)
        .filter(Document.user_id == user_id)
        .order_by(Document.uploaded_at.desc())
        .limit(3)
        .all()
    )
    if docs:
        lines = ["【アップロードされた資料（直近）】"]
        for doc in docs:
            doc_type = cast(Optional[str], doc.doc_type)
            period_label = cast(Optional[str], doc.period_label)
            filename = cast(Optional[str], doc.filename)
            title = cast(Optional[str], getattr(doc, "title", None))

            meta_parts: List[str] = []
            if doc_type:
                meta_parts.append(doc_type)
            if period_label:
                meta_parts.append(period_label)
            meta = " / ".join(meta_parts) if meta_parts else ""
            resolved_title = title or filename or "無題"
            suffix = f"（{meta}）" if meta else ""
            lines.append(f"- {resolved_title}{suffix}")
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
        done=current_step_int >= 5,
    )


async def run_guided_chat(payload: ChatTurnRequest, db: Session) -> ChatTurnResponse:
    if not payload.message and not payload.selected_option_id and not payload.selection and not payload.messages:
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
    free_text = payload.message

    if selection:
        if selection.type == "choice":
            choice_id = selection.id
            choice_label = selection.label
        elif selection.type == "free_text":
            free_text = selection.text or payload.message

    if not selection:
        choice_id = payload.selected_option_id
        if not free_text and payload.messages:
            # use last user message from messages array
            for m in reversed(payload.messages):
                if m.role == "user" and m.content:
                    free_text = m.content
                    break

    if not free_text and not choice_label and not choice_id:
        raise HTTPException(status_code=400, detail="入力が空です。メッセージまたは選択肢を送信してください")

    option_label = choice_label or (choice_id and _find_option_label(history, choice_id))
    display_text = free_text or option_label or choice_id or ""
    case_query_text = display_text or ""
    is_case_query = any(keyword in case_query_text for keyword in CASE_KEYWORDS)

    user_entries: List[str] = []
    if choice_id:
        user_entries.append(f"[choice_id:{choice_id}] {display_text}")
    elif display_text:
        user_entries.append(display_text.strip())

    for text in user_entries:
        saved = _persist_message(db, conversation, "user", text)
        history.append(saved)

    if not conversation.main_concern and user_entries:
        conversation.main_concern = user_entries[0][:255]

    query_text = free_text or option_label or conversation.main_concern or (payload.category or "経営に関する相談")
    # augment query with domain hints to hit relevant chapters
    extra_terms: List[str] = []
    text_for_hint = (display_text or "") + " " + (choice_label or "") + " " + (payload.category or "")
    if any(k in text_for_hint for k in ["売上", "販売", "需要", "価格", "販路"]):
        extra_terms.extend(["売上", "需要", "価格転嫁", "付加価値", "販路"])
    if any(k in text_for_hint for k in ["採用", "人材", "人手", "人材不足"]):
        extra_terms.extend(["人手不足", "賃上げ", "省力化", "外部人材", "デジタル化"])
    if any(k in text_for_hint for k in ["資金", "資金繰り", "キャッシュ", "借入"]):
        extra_terms.extend(["資金繰り", "キャッシュフロー", "借入", "返済", "補助金"])
    if extra_terms:
        query_text = f"{query_text} " + " ".join(extra_terms)

    try:
        rag_chunks = await rag_service.retrieve_context(
            db=db,
            user_id=cast(Optional[str], user.id) if user else None,
            company_id=payload.company_id,
            query=query_text,
            top_k=5,
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

    # knowledge search
    citations: List[Citation] = []
    hits_payload: List[dict] = []
    hits_for_examples: List[dict] = []
    try:
        knowledge_hits = await search_knowledge(query_text, top_k=8)
        keywords = [k for k in ["売上", "需要", "価格", "販路", "採用", "人材", "人手", "人材不足", "資金", "資金繰り", "キャッシュ", "賃上げ", "省力化", "外部人材", "デジタル"] if k in query_text]
        filtered_hits = [
            h
            for h in knowledge_hits
            if any(
                kw in (h.get("snippet") or "") or kw in (h.get("source_title") or "")
                for kw in keywords
            )
        ] if keywords else []
        hits_for_use = (filtered_hits or knowledge_hits)[:8]
        hits_for_examples = hits_for_use
        for idx, hit in enumerate(hits_for_use, 1):
            txt = hit.get("snippet") or hit.get("text") or ""
            title = hit.get("source_title") or ""
            page = hit.get("page")
            path = hit.get("source_path")
            score = hit.get("score")
            excerpt = txt if len(txt) <= 400 else txt[:400] + "..."
            all_chunks.append(f"[参考{idx}] {title} p.{page or '?'}\n{excerpt}")
            citations.append(
                Citation(
                    title=title,
                    page=page,
                    path=path,
                    score=score,
                    snippet=txt,
                )
            )
            hits_payload.append(
                {
                    "title": title or (hit.get("title") or hit.get("source_path") or ""),
                    "path": path,
                    "page": page,
                    "score": score,
                    "snippet": txt,
                }
            )
        if hits_for_use:
            logger.info("[knowledge] candidates=%s top_score=%s", len(hits_for_use), hits_for_use[0].get("score"))
    except Exception:
        logger.exception("knowledge search failed")

    case_answer: Optional[str] = None
    if is_case_query:
        try:
            case_answer = build_examples_answer(case_query_text or query_text, hits_for_examples)
        except Exception:
            logger.exception("failed to build case-style answer")
            case_answer = "現在混雑しています。もう一度お試しください。"

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

    messages: List[ChatMessage] = [
        cast(ChatMessage, {"role": "system", "content": SYSTEM_PROMPT}),
        cast(ChatMessage, {"role": "user", "content": user_prompt_text}),
    ]

    prior_step_value = conversation.step or 0
    try:
        prior_step_int = int(prior_step_value)
    except (TypeError, ValueError):
        prior_step_int = 0

    used_fallback = False
    try:
        llm_result = await chat_json_safe("LLM-CHAT-01-v1", messages, max_tokens=400, temperature=0.25)
        if not llm_result.ok or not isinstance(llm_result.value, dict):
            logger.warning("guided chat: LLM failed (%s)", llm_result.error)
            used_fallback = True
            result = _build_fallback_response(conversation)
        else:
            raw = dict(llm_result.value)
            raw.setdefault("options", [])
            raw.setdefault("allow_free_text", True)
            raw["citations"] = [c.model_dump() for c in citations] if citations else []

            raw.pop("conversation_id", None)
            raw.pop("step", None)

            next_step = prior_step_int + 1
            if next_step > 5:
                next_step = 5

            raw["step"] = next_step
            raw["done"] = next_step >= 5

            result = ChatTurnResponse(conversation_id=conversation.id, **raw)
    except AzureNotConfiguredError:
        logger.exception("Azure OpenAI is not configured; using fallback response")
        used_fallback = True
        result = _build_fallback_response(conversation)
    except HTTPException:
        logger.exception("HTTPException from LLM client; using fallback response")
        used_fallback = True
        result = _build_fallback_response(conversation)
    except Exception:
        logger.exception("guided chat generation failed; using fallback response")
        used_fallback = True
        result = _build_fallback_response(conversation)

    result.citations = citations
    result.hits = hits_payload
    if case_answer is not None:
        result.answer = case_answer
    logger.info("[guided] citations_len=%s", len(citations))

    conversation.step = prior_step_int if used_fallback else result.step
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
