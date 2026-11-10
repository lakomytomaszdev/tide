// „Przyjmij odstępstwo…” przy błędzie kroku domknięcia (K-97, PT-339; K-70 pkt 13). Usługa nie ma jeszcze osobnej
// metody przyjęcia odstępstwa (szkic toru silnika, pkt 1), ale ma ścieżkę: `ResolveCardStep {choice: "retry", step,
// answer}` wznawia krok domknięcia z instrukcją użytkownika, a kartka domknięcia mówi, że o przyjęciu odstępstwa
// decyduje użytkownik. Okno dokłada więc własną możliwość — jawną, nigdy niepoleconą i niezaznaczoną — i składa
// `answer` ze stałej instrukcji i uzasadnienia.
//
// Okno niczego nie liczy po swojemu: możliwość stoi tylko przy decyzji, w której usługa sama daje możliwość `retry`
// (decyzja błędu powstaje dopiero po automatycznym ponowieniu albo przy wygasłym logowaniu; zakończona karta nie ma
// decyzji), a żądanie to parametry tej możliwości usługi z dopisanym `answer`. Okno nie rozpoznaje „jednego
// niestabilnego testu” — opis mówi, że to wybór na niestabilny test z rozliczeniem.
.pragma library

// Id możliwości w formularzu (nie koliduje z id usługi ani z „Inną odpowiedzią…”).
var OPTION_ID = "__deviation__"
// Limit odpowiedzi usługi (engine: `retry` z `answer` do 4000 znaków).
var LIMIT = 4000

var TITLE = "Przyjmij odstępstwo…"
var EFFECT = "Wybór na niestabilny test z rozliczeniem. Krok domknięcia wznowi się z tą decyzją i zapisze rozliczenie "
             + "odstępstwa w podsumowaniu; wynik testów zostaje porażką, a scalenie do gałęzi głównej nadal wymaga "
             + "osobnej zgody."
var FIELD_LABEL = "Uzasadnienie odstępstwa"
var HINT = "Np. „test_x pada też na bazie i na głównej gałęzi przy obciążeniu; poprawka w osobnej karcie”."

// Stała instrukcja dla wznowionego kroku domknięcia — zgodna z „Rozliczeniem nieudanych przebiegów”
// (docs/testing.md, „Wyjątki od testów”) i z kartką domknięcia (handoff.close_tests).
var INSTRUCTION = "Użytkownik przyjmuje odstępstwo od pełnych testów domknięcia. "
    + "Zapisz w podsumowaniu rozliczenie według zasad „Wyjątki od testów” projektu: pierwszy wynik (polecenie, SHA, "
    + "nieudane testy, odnośnik do logu), porównanie tego samego testu na przypiętej bazie i na gałęzi głównej "
    + "(z SHA i warunkami), obciążenie maszyny, dokładny zakres odstępstwa (które niepowodzenia obejmuje, a które "
    + "nadal blokują) oraz właściciela poprawki i sposób jej śledzenia; brak danych oznacz jawnie. "
    + "Wynik testu zostaje porażką — nie opisuj go jako udanego. "
    + "Nie powtarzaj pełnego przebiegu na tym samym kodzie. "
    + "Scalenie do gałęzi głównej wymaga osobnej zgody użytkownika; to odstępstwo jej nie daje. "
    + "Uzasadnienie użytkownika: „"
var CLOSING = "”"

// Możliwość `retry` usługi dla kroku tej decyzji; null — usługa nie dopuszcza ponowienia.
function retryOption(decision) {
    var options = decision && Array.isArray(decision.options) ? decision.options : []
    for (var i = 0; i < options.length; i++) {
        var o = options[i]
        if (o && o.method && o.params && o.params.choice === "retry" && o.params.step === decision.step) return o
    }
    return null
}

// Czy decyzja ma możliwość „Przyjmij odstępstwo…”: błąd kroku domknięcia, dla którego usługa dopuszcza ponowienie.
function offered(decision) {
    if (!decision || decision.group === true) return false
    if (decision.kind !== "error" || decision.stepKind !== "close" || !decision.step) return false
    return retryOption(decision) !== null
}

function clean(reason) {
    return reason === null || reason === undefined ? "" : String(reason).trim()
}

// Pełna odpowiedź dla kroku domknięcia: stała instrukcja i uzasadnienie.
function answer(reason) {
    return INSTRUCTION + clean(reason) + CLOSING
}

// Licznik pod polem: długość całej odpowiedzi wobec limitu usługi.
function counter(reason) {
    return answer(reason).length + " / " + LIMIT + " znaków (z instrukcją dla kroku)"
}

function tooLong(reason) {
    return answer(reason).length > LIMIT
}

// Zdanie, dlaczego „Wybierz” jest nieaktywne; pusty napis — można wybrać.
function blocker(reason) {
    if (clean(reason) === "") return "Wpisz uzasadnienie — bez niego odstępstwa nie da się przyjąć."
    if (tooLong(reason))
        return "Uzasadnienie jest za długie: cała odpowiedź z instrukcją może mieć najwyżej " + LIMIT + " znaków."
    return ""
}

// Żądanie do usługi: metoda i parametry możliwości `retry` decyzji (z jej `step`) z dopisanym `answer`;
// null — bez możliwości `retry` albo z uzasadnieniem, którego nie da się wysłać.
function request(decision, reason) {
    if (!offered(decision) || blocker(reason) !== "") return null
    var option = retryOption(decision)
    var params = {}
    for (var key in option.params) params[key] = option.params[key]
    params.answer = answer(reason)
    return { method: option.method, params: params }
}
