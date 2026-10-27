// Nazwy modeli wspólne dla okien i widżetów Tide (biblioteka „tide”, kopiowana przez widgets/stage.py do każdego
// pakietu). Reguła chmury Ollamy taka sama jak w usłudze: daemon/tide_daemon/modelnames.py (is_cloud_model).
.pragma library

// Model chmurowy Ollamy (`gpt-oss:cloud`, `gpt-oss:120b-cloud`, `kimi-k2:1t-CLOUD`): etykieta po ostatnim „:”
// kończy się na „cloud” po „:” albo „-”, bez względu na wielkość liter; nie: `mycloud:7b`, `qwen3:cloudless`,
// `x:7b-cloudy`, gołe `cloud`. Białe znaki na brzegach obcinamy (nazwa z pola tekstowego). Lokalna Ollama tylko
// przekazuje taki model na ollama.com — Tide nie używa go jako lokalnego.
function isCloudModel(name) {
    if (typeof name !== "string") return false
    var text = name.trim()
    var i = text.lastIndexOf(":")
    return i >= 0 && /[:-]cloud$/i.test(":" + text.slice(i + 1))
}
