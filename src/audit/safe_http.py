"""진단 트래픽 전용 HTTP opener (UPGRADE_PLAN.md H5·H6).

urllib 의 기본 opener(`urllib.request.urlopen`)는 두 가지를 암묵적으로 한다.

1. 3xx 리다이렉트를 자동으로 따라간다 — 따라간 목적지는 정책 계층(`policy.decide`, 프록시의 범위 검사)을
   거치지 않는다. 범위 안 주소의 302 한 번으로 범위 밖 호스트에 요청이 나가고 그 응답이 증거로 저장된다
   (CLAUDE.md §8-9 "리다이렉트/최종 목적지 재검증" 위반, H5).
2. OS 의 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` 환경 변수를 따른다 — 시스템 프록시가 설정된 PC 에서는 진단
   트래픽이 운영자가 의도하지 않은 프록시를 거쳐 나간다(§8-8, H6).

이 모듈의 opener 는 둘 다 하지 않는다. 3xx 는 따라가지 않고 `HTTPError` 로 그대로 돌려준다 — 호출자가 그
응답(Location 포함)을 기록하거나 클라이언트에 그대로 넘기면, 클라이언트가 Location 으로 다시 요청할 때 그
요청이 정책/범위 검사를 새로 받는다(HD3: 정책 경로를 하나로 유지). 업스트림 프록시는 호출자가 명시적으로
넘길 때만 쓴다(`config/workbench.json` 의 `upstream_proxy`).

진단 트래픽이 아닌 호출(웹훅 알림, LLM API)은 이 모듈을 쓰지 않는다.
"""
from __future__ import annotations

import ssl
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx 를 따라가지 않는다. None 을 돌려주면 urllib 이 원래 3xx 응답을 HTTPError 로 올린다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 — urllib 훅
        return None


def opener(upstream_proxy: str | None = None,
           ssl_context: ssl.SSLContext | None = None) -> urllib.request.OpenerDirector:
    """리다이렉트를 따라가지 않고, 환경 변수 프록시를 무시하는 opener.

    upstream_proxy 를 주면(예: "http://10.0.0.5:3128") http/https 둘 다 그 프록시로만 보낸다. 주지 않으면
    `ProxyHandler({})` 로 환경 변수 프록시를 명시적으로 끈다(빈 dict 가 아니면 urllib 이 환경 변수를 읽는다).
    ssl_context 를 주지 않으면 기본(인증서 검증하는) 컨텍스트를 쓴다 — 전역 `install_opener()` 설정은 이
    opener 에 영향을 주지 않는다.
    """
    proxies = {"http": upstream_proxy, "https": upstream_proxy} if upstream_proxy else {}
    handlers = [urllib.request.ProxyHandler(proxies), _NoRedirect()]
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    return urllib.request.build_opener(*handlers)
