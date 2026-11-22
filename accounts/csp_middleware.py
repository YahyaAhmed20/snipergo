class CSPReportOnlyMiddleware:
    """
    يضيف ترويسة Content-Security-Policy-Report-Only إلى جميع الاستجابات.
    وضع Report-Only لا يحظر أي مورد؛ بل يراقب الانتهاكات فقط.
    لا يُضبط report-uri لأن لا يوجد نقطة نهاية فعالة لاستقبال التقارير ومعالجتها بأمان.
    """

    CSP_POLICY = (
        "default-src 'self';"
        "script-src 'self' 'unsafe-inline';"
        "style-src 'self' 'unsafe-inline';"
        "img-src 'self' data: tile.openstreetmap.org;"
        "connect-src 'self' wss: https://photon.komoot.io;"
        "frame-src 'none';"
        "base-uri 'self';"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # لا ن overwrite ترويسة موجودة مسبقاً لضمان عدم التعارض
        if "Content-Security-Policy-Report-Only" not in response:
            response["Content-Security-Policy-Report-Only"] = self.CSP_POLICY
        return response
