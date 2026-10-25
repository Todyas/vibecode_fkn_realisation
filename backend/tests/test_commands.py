from app.commands import find_key, is_command, norm_key, parse


def test_plain_start():
    c = parse("/no_idea start Онбординг")
    assert (c.sub, c.name, c.rest, c.quoted) == ("start", "Онбординг", "", False)


def test_botname_suffix_and_quotes():
    c = parse('/no_idea@no_idea_hack_bot ask "Редизайн онбординга" что решили по срокам?')
    assert c.sub == "ask"
    assert c.name == "Редизайн онбординга"
    assert c.rest == "что решили по срокам?"
    assert c.quoted


def test_guillemets():
    c = parse("/no_idea start «Поднять продажи»")
    assert c.name == "Поднять продажи" and c.quoted


def test_first_word_name():
    c = parse("/no_idea ask Продажи что решили по скидке?")
    assert c.name == "Продажи" and c.rest == "что решили по скидке?"
    assert c.raw_args == "Продажи что решили по скидке?"


def test_no_name():
    c = parse("/no_idea stop")
    assert c.sub == "stop" and c.name is None


def test_help_variants():
    assert parse("/no_idea").sub == "help"
    assert parse("/no_idea help").sub == "help"
    assert parse("/no_idea blabla").sub == "help"
    assert parse("/NO_IDEA LIST").sub == "list"


def test_is_command():
    assert is_command("/no_idea start x")
    assert is_command("/no_idea@MyBot start x", "mybot")
    assert not is_command("/no_idea@OtherBot start x", "mybot")
    assert not is_command("/no_ideas")
    assert not is_command("привет /no_idea")
    assert not is_command(None)


def test_find_key():
    keys = ["продажи", "онбординг", "онбординг 2"]
    assert find_key(keys, "  ПРОДАЖИ ") == "продажи"
    assert find_key(keys, "прод") == "продажи"
    assert find_key(keys, "онбординг") == "онбординг"   # точное важнее префикса
    assert find_key(keys, "онб") is None                # два кандидата
    assert find_key(keys, "нет") is None
    assert norm_key(" Редизайн   Онбординга ") == "редизайн онбординга"


def test_split_role():
    from app.bot import split_role
    assert split_role("Маркетолог: нужна рассылка", "Bodya") == ("Маркетолог", "нужна рассылка")
    assert split_role("просто текст", "Bodya") == ("Bodya", "просто текст")
    assert split_role("в 10:30 созвон", "Bodya") == ("Bodya", "в 10:30 созвон")


def test_extract_json_think():
    from app.llm import extract_json
    assert extract_json('<think>хм {"a":0}</think>\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Вот ответ: {"a": 2} готово') == '{"a": 2}'
