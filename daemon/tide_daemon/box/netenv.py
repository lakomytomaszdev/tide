"""Zmienne proxy w środowisku pudełka — jedno źródło dla budowy (#4), obrazu bazowego (#16), kroku (#6) i stosów (#29).

`BOX_NO_PROXY` to wynik P-6 (`docs/box-prototype.md`, „NO_PROXY per klient”): pętla zwrotna pudełka (serwery testów,
`ng serve`), sieci Dockera w pudełku (`172.16.0.0/12`, Testcontainers) i `host.docker.internal`. Wszystko inne idzie
przez proxy Tide na adresie mostka. Wartość wstrzykuje usługa (builder, krok) — skrypty powłoki jej nie wpisują.
"""
BOX_NO_PROXY = "localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal"


def proxy_env(proxy: str, no_proxy: str = BOX_NO_PROXY) -> dict:
    """`HTTP(S)_PROXY` i `NO_PROXY` (oba warianty wielkości liter — klienci czytają różne) dla `proxy`."""
    return {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy,
            "NO_PROXY": no_proxy, "no_proxy": no_proxy}
