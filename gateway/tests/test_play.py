import asyncio
from stackchan_gateway import play, tools


def test_adventure_persists_and_requires_valid_choice(tmp_path, monkeypatch):
    monkeypatch.setenv('STACKCHAN_PLAY_DB', str(tmp_path / 'play.db'))
    assert play.adventure('开始')['choices'] == ['推门', '观察']
    assert 'error' in play.adventure('开始')
    assert 'error' in play.adventure('选择', '乱选')
    assert '钥匙' in play.adventure('选择', '观察')['story']
    assert '钥匙' in play.adventure('继续')['story']
    assert play.adventure('选择', '拿起')['finished']
    play.adventure('重置')
    assert 'error' in play.adventure('继续')


def test_guess_and_role(tmp_path, monkeypatch):
    monkeypatch.setenv('STACKCHAN_PLAY_DB', str(tmp_path / 'play.db'))
    monkeypatch.setattr(play.random, 'randint', lambda a, b: 42)
    play.guess('开始')
    assert play.guess('猜', '30')['message'] == '太小了！'
    assert play.guess('猜', '42')['correct']
    assert 'error' in play.guess('猜', '42')
    assert play.role('船长')['role'] == '船长'
    assert play.role()['role'] == '船长'
    assert 'error' in play.role('不存在')
    assert asyncio.run(tools.execute('play_adventure', {'action': '开始'}))['choices']
