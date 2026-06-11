class SwaggerSafeQuerysetMixin:
    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            model = getattr(self, 'queryset', None)
            return model.model.objects.none() if model is not None else []

        user = getattr(self.request, 'user', None)
        if user is None or not user.is_authenticated:
            if hasattr(self, 'queryset') and self.queryset is not None:
                return self.queryset.model.objects.none()
            if hasattr(self, 'serializer_class') and self.serializer_class is not None:
                meta = getattr(self.serializer_class, 'Meta', None)
                if meta and hasattr(meta, 'model'):
                    return meta.model.objects.none()
            return []

        return super().get_queryset()