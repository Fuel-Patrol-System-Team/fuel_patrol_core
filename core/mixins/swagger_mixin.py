class SwaggerSafeQuerysetMixin:
    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset.model.objects.none() if self.queryset else []
        return super().get_queryset()