from aios_copilot import voice
from aios_copilot.parlato import in_words, number


def test_numbers():
    words = {1: "uno", 3: "tre", 21: "ventuno", 23: "ventitré", 28: "ventotto", 103: "centotré", 108: "centotto",
             180: "centottanta", 1000: "mille", 1003: "milletré", 2026: "duemilaventisei", 21000: "ventunmila",
             1_000_000: "un milione"}
    assert {n: number(n) for n in words} == words


def test_prices_times_dates_percent():
    assert in_words("luglio 84,20 euro, agosto 91,10 €") == "luglio ottantaquattro euro e venti, agosto novantuno euro e dieci"
    assert in_words("Totale 1.234,50 € (IVA 22%), 1 euro, 0,50 €") == \
        "Totale milleduecentotrentaquattro euro e cinquanta (IVA ventidue per cento), un euro, cinquanta centesimi"
    assert in_words("alle 10:30 e alle 9:00 del 12/10, poi il 1/11/2026") == \
        "alle dieci e trenta e alle nove del dodici ottobre, poi il primo novembre duemilaventisei"
    assert in_words("ho 3 mail e 2,5 kg") == "ho tre mail e due virgola cinque kg"


def test_codes_and_phones_stay_readable():
    assert in_words("targa GH123KL, codice SCR-01, contratto IT001E12345678") == "targa GH123KL, codice SCR-01, contratto IT001E12345678"
    assert in_words("chiama lo 051 123456") == "chiama lo zero cinque uno, uno due tre quattro cinque sei"
    assert voice.for_speech("Hai speso **84,20 €** alle 18:45.") == "Hai speso ottantaquattro euro e venti alle diciotto e quarantacinque."
