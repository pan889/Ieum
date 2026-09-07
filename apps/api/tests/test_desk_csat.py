"""만족도 조사 (feature-map C11).

토큰과 값 다듬기는 순수 함수라 손으로 적어 못 박고, 보내는 판단과 답을 받는
층은 실제 DB 로 본다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **한 번만 보낸다.** 티켓이 다시 열렸다 닫힐 때마다 또 보내면, 안 그래도 안
  답한 사람에게 조르는 꼴이 된다.
- **답도 한 번만 받는다.** 두 번째 제출이 첫 점수를 덮으면 "며칠 뒤 화가 나서
  다시 눌렀다" 가 조용히 이긴다.
- **토큰이 근거다.** 로그인 없이 열리므로 위조와 만료가 이 파일의 자물쇠다.
- **반송된 주소에는 안 보낸다.** 회신과 같은 판단이다 (C6).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.config import Settings
from ieum.core.events import EventEnvelope
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, set_permission_service
from ieum.core.time import utcnow
from ieum.modules.desk.csat import (
    clean_answer,
    decode_survey_token,
    encode_survey_token,
)
from ieum.modules.desk.models import TicketExt
from ieum.modules.desk.service import CsatService
from ieum.modules.desk.survey import SurveyContext, collect_survey_mail
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver

TRANSITIONED = "issue.transitioned"


@pytest.fixture(autouse=True)
def permissions() -> PermissionService:
    """**조사 자체는 권한을 안 본다** — 근거가 토큰이기 때문이다.

    그래도 배선은 필요하다: 티켓 계약(`issues.get_tickets`)이 조회 전용
    서비스를 만드는 데 쓴다. 앱에서는 `create_app` 이 늘 배선하므로 이건
    시험 환경의 일이다.
    """
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _ticket(
    session: AsyncSession, *, guest: bool = False, customer_locale: str = "ko"
) -> tuple[Project, Issue, TicketExt]:
    project = Project(key=f"S{new_id().hex[-6:].upper()}", name="Survey")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    state = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    session.add(state)
    issue_type = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
    session.add(issue_type)
    await session.flush()

    issue = Issue(
        project_id=project.id,
        type_id=issue_type.id,
        state_id=state.id,
        key_seq=1,
        summary="프린터가 안 됩니다",
    )
    session.add(issue)
    await session.flush()

    ticket = TicketExt(issue_id=issue.id, channel="portal")
    if guest:
        ticket.guest_email = f"g-{new_id().hex[-8:]}@example.com"
        ticket.guest_name = "손님"
    else:
        customer = User(
            email=f"c-{new_id()}@example.com",
            display_name="고객",
            status="active",
            is_customer=True,
            locale=customer_locale,
        )
        session.add(customer)
        await session.flush()
        ticket.reporter_customer_id = customer.id
    session.add(ticket)
    await session.flush()
    return project, issue, ticket


def _closed(issue: Issue, project: Project) -> EventEnvelope:
    return EventEnvelope(
        id=new_id(),
        event_type=TRANSITIONED,
        aggregate_type="issue",
        aggregate_id=issue.id,
        payload={
            "issue_key": f"{project.key}-{issue.key_seq}",
            "to_state_category": "done",
            "from_state": "Open",
            "to_state": "Done",
        },
    )


class TestTheToken:
    def test_it_round_trips(self, settings: Settings) -> None:
        issue_id = new_id()
        assert decode_survey_token(encode_survey_token(issue_id, settings), settings) == issue_id

    def test_a_forged_token_is_refused(self, settings: Settings) -> None:
        with pytest.raises(ValidationError) as exc:
            decode_survey_token("not-a-real-token", settings)
        assert exc.value.code == "desk.csat_token_invalid"

    def test_an_expired_token_is_refused(self, settings: Settings) -> None:
        token = encode_survey_token(new_id(), settings, ttl_seconds=-1)
        with pytest.raises(ValidationError) as exc:
            decode_survey_token(token, settings)
        assert exc.value.code == "desk.csat_token_expired"

    def test_a_token_from_another_purpose_is_refused(self, settings: Settings) -> None:
        """**같은 비밀키라도 용도가 다르면 못 연다.**

        초대 토큰을 조사 링크에 붙여 넣으면 사용자 id 를 티켓 id 로 읽게
        되는데, `SecretBox` 의 용도 분리가 그것을 막는다.
        """
        from ieum.modules.identity.invites import encode_invite_token

        with pytest.raises(ValidationError):
            decode_survey_token(encode_invite_token(new_id(), settings), settings)


class TestCleaningTheAnswer:
    def test_a_good_answer_passes(self) -> None:
        assert clean_answer(4, " 빨랐습니다 ") == (4, "빨랐습니다")

    def test_a_score_outside_the_scale_is_refused(self) -> None:
        for bad in (0, 6, -1):
            with pytest.raises(ValidationError) as exc:
                clean_answer(bad, None)
            assert exc.value.code == "desk.csat_score_invalid"

    def test_a_boolean_is_not_a_score(self) -> None:
        # `True` 는 파이썬에서 `int` 이고 `1 in SCORES` 다. 체크박스 하나가
        # 1점이 되게 두지 않는다.
        with pytest.raises(ValidationError):
            clean_answer(True, None)

    def test_a_missing_score_is_refused(self) -> None:
        # 한마디만 남기는 답을 받으면 평균의 분모를 말할 수 없다.
        with pytest.raises(ValidationError):
            clean_answer(None, "좋았어요")

    def test_an_empty_comment_becomes_none(self) -> None:
        assert clean_answer(5, "   ") == (5, None)

    def test_a_long_comment_is_refused(self) -> None:
        with pytest.raises(ValidationError) as exc:
            clean_answer(5, "가" * 1001)
        assert exc.value.code == "desk.csat_comment_too_long"


class TestSendingIt:
    async def test_closing_a_ticket_queues_one_survey(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        project, issue, ticket = await _ticket(session)
        mails = await collect_survey_mail(
            SurveyContext(session=session, settings=settings), _closed(issue, project)
        )
        assert len(mails) == 1
        assert mails[0].link is not None
        assert mails[0].link.startswith("/survey?token=")
        # 표시가 남는다. 이게 "한 번만" 의 근거다.
        assert ticket.csat_sent_at is not None

    async def test_it_does_not_send_twice(self, session: AsyncSession, settings: Settings) -> None:
        """티켓이 다시 열렸다 닫혀도 또 보내지 않는다."""
        project, issue, _ = await _ticket(session)
        ctx = SurveyContext(session=session, settings=settings)
        assert len(await collect_survey_mail(ctx, _closed(issue, project))) == 1
        assert await collect_survey_mail(ctx, _closed(issue, project)) == []

    async def test_moving_to_another_category_sends_nothing(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        project, issue, _ = await _ticket(session)
        envelope = _closed(issue, project)
        envelope.payload["to_state_category"] = "in_progress"
        assert (
            await collect_survey_mail(SurveyContext(session=session, settings=settings), envelope)
            == []
        )

    async def test_a_bounced_address_gets_nothing(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        # 검증되지 않은 게스트 주소로 계속 보내면, 남의 주소를 적어 넣은
        # 경우 그 사람에게 계속 배달을 시도한다 (C6 와 같은 판단).
        project, issue, ticket = await _ticket(session, guest=True)
        ticket.email_bounced_at = utcnow()
        await session.flush()
        assert (
            await collect_survey_mail(
                SurveyContext(session=session, settings=settings), _closed(issue, project)
            )
            == []
        )

    async def test_a_ticket_with_no_requester_gets_nothing(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """상담원이 대신 넣은 티켓에는 물을 상대가 없다."""
        project, issue, ticket = await _ticket(session)
        ticket.reporter_customer_id = None
        ticket.channel = "agent"
        await session.flush()
        assert (
            await collect_survey_mail(
                SurveyContext(session=session, settings=settings), _closed(issue, project)
            )
            == []
        )

    async def test_an_issue_that_is_not_a_ticket_gets_nothing(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        project, issue, ticket = await _ticket(session)
        await session.delete(ticket)
        await session.flush()
        assert (
            await collect_survey_mail(
                SurveyContext(session=session, settings=settings), _closed(issue, project)
            )
            == []
        )

    async def test_it_writes_in_the_customers_language(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """티켓을 닫은 상담원의 언어가 아니다 (i18n.md 3절)."""
        project, issue, _ = await _ticket(session, customer_locale="en")
        [mail] = await collect_survey_mail(
            SurveyContext(session=session, settings=settings), _closed(issue, project)
        )
        assert "How did we do" in mail.subject


class TestAnsweringIt:
    async def test_the_customer_answers_without_logging_in(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        project, issue, ticket = await _ticket(session)
        token = encode_survey_token(issue.id, settings)
        service = CsatService(session, settings)

        found = await service.view(token)
        assert found.issue_key == f"{project.key}-{issue.key_seq}"
        assert found.score is None

        answered = await service.answer(token, score=5, comment="빨랐습니다")
        assert answered.score == 5
        assert ticket.csat_score == 5
        assert ticket.csat_comment == "빨랐습니다"

    async def test_a_second_answer_is_refused(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """첫 점수를 덮지 않는다 — 며칠 뒤 화가 나서 다시 누른 것이 조용히
        이기면, 우리가 재는 것이 무엇인지 말할 수 없다."""
        _, issue, ticket = await _ticket(session)
        token = encode_survey_token(issue.id, settings)
        service = CsatService(session, settings)
        await service.answer(token, score=5, comment=None)
        with pytest.raises(ConflictError) as exc:
            await service.answer(token, score=1, comment="다시 생각해 보니")
        assert exc.value.code == "desk.csat_already_answered"
        assert ticket.csat_score == 5

    async def test_a_forged_token_answers_nothing(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        with pytest.raises(ValidationError):
            await CsatService(session, settings).answer("nope", score=5, comment=None)

    async def test_a_token_for_a_gone_ticket_is_not_found(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        token = encode_survey_token(new_id(), settings)
        with pytest.raises(NotFoundError):
            await CsatService(session, settings).view(token)

    async def test_the_view_does_not_carry_the_conversation(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """링크는 메일로 나가고 그 메일은 전달될 수 있다."""
        _, issue, _ = await _ticket(session)
        found = await CsatService(session, settings).view(encode_survey_token(issue.id, settings))
        assert set(found.__slots__) == {"issue_key", "summary", "score", "comment"}

    async def test_a_link_that_is_a_month_old_is_refused(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        # 그때의 기분을 지금 묻는 셈이다.
        _, issue, _ = await _ticket(session)
        old = encode_survey_token(
            issue.id, settings, ttl_seconds=-int(timedelta(days=1).total_seconds())
        )
        with pytest.raises(ValidationError) as exc:
            await CsatService(session, settings).view(old)
        assert exc.value.code == "desk.csat_token_expired"
