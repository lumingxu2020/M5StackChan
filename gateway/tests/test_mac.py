from unittest.mock import patch

from stackchan_gateway import mac


def test_reminder_list_name_is_passed_as_osascript_argument():
    hostile = 'x"\nend tell\ndo shell script "false"'
    with patch("stackchan_gateway.mac._command_exists", return_value=True), patch(
        "stackchan_gateway.mac._run"
    ) as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""

        result = mac.reminders_pending(hostile)

    command = run.call_args.args[0]
    script = command[2]
    assert result == {"list": hostile, "items": []}
    assert hostile not in script
    assert command[-1] == hostile
    assert command[-2] == "--"
