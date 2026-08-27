from linebot_meeting.minutes import ActionItem, MeetingMinutes, render_minutes


def test_render_minutes() -> None:
    result = render_minutes(
        MeetingMinutes(
            title="週會",
            participants=["Amy", "王小明"],
            summary=["確認上線範圍"],
            decisions=["週五上線"],
            action_items=[ActionItem(owner="Amy", task="補測試", deadline="週四")],
        )
    )
    assert "📝 週會" in result
    assert "Amy、王小明" in result
    assert "補測試｜負責：Amy｜期限：週四" in result
