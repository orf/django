import re
from functools import lru_cache

from django.conf import settings
from django.http import HttpResponsePermanentRedirect
from django.utils.deprecation import MiddlewareMixin


@lru_cache(maxsize=32)
def create_csp_policy(base):
    base = dict(base)
    report_uri = base.pop('report-uri')

    policy = {}
    for key, parts in base.items():
        policy[key] = ' '.join(p if p is not False else "'none'" for p in parts)

    if report_uri:
        policy['report-uri'] = report_uri

    return '; '.join(
        "{0} {1}".format(key, value)
        for key, value in policy.items()
    )


class SecurityMiddleware(MiddlewareMixin):
    def __init__(self, get_response=None):
        self.sts_seconds = settings.SECURE_HSTS_SECONDS
        self.sts_include_subdomains = settings.SECURE_HSTS_INCLUDE_SUBDOMAINS
        self.sts_preload = settings.SECURE_HSTS_PRELOAD
        self.content_type_nosniff = settings.SECURE_CONTENT_TYPE_NOSNIFF
        self.xss_filter = settings.SECURE_BROWSER_XSS_FILTER
        self.redirect = settings.SECURE_SSL_REDIRECT
        self.redirect_host = settings.SECURE_SSL_HOST
        self.redirect_exempt = [re.compile(r) for r in settings.SECURE_REDIRECT_EXEMPT]
        self.csp_policy = settings.CSP_POLICY
        self.csp_report_policy = settings.CSP_REPORT_ONLY_POLICY
        self.get_response = get_response

    def process_request(self, request):
        path = request.path.lstrip("/")
        if (self.redirect and not request.is_secure() and
                not any(pattern.search(path)
                        for pattern in self.redirect_exempt)):
            host = self.redirect_host or request.get_host()
            return HttpResponsePermanentRedirect(
                "https://%s%s" % (host, request.get_full_path())
            )

    def process_response(self, request, response):
        if (self.sts_seconds and request.is_secure() and
                'Strict-Transport-Security' not in response):
            sts_header = "max-age=%s" % self.sts_seconds
            if self.sts_include_subdomains:
                sts_header = sts_header + "; includeSubDomains"
            if self.sts_preload:
                sts_header = sts_header + "; preload"
            response['Strict-Transport-Security'] = sts_header

        if self.content_type_nosniff:
            response.setdefault('X-Content-Type-Options', 'nosniff')

        if self.xss_filter:
            response.setdefault('X-XSS-Protection', '1; mode=block')

        if self.csp_policy:
            policy = create_csp_policy(tuple(self.csp_policy.items()))
            response.setdefault('Content-Security-Policy', policy)

        if self.csp_report_policy:
            report_policy = create_csp_policy(tuple(self.csp_report_policy.items()))
            response.setdefault('Content-Security-Policy-Report-Only', report_policy)

        return response
