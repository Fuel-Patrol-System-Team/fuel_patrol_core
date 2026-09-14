import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

import django
django.setup()

from core.models import Car, CarModel, CarModelSpecification


if __name__ == "__main__":
    model = CarModel.objects.first()
    modelspec =CarModelSpecification.objects.first()
    cars = Car.objects.update(model=model, model_specs=modelspec)
