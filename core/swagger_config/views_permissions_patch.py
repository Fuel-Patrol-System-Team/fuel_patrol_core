"""
Патч для views.py — добавить IsNotDemoUser к мутирующим и parsing вьюхам.

Показаны только изменённые permission_classes.
Все остальные вьюхи остаются без изменений (IsOrgMember уже требует auth).

ВАЖНО: IsNotDemoUser добавляется ПЕРВЫМ чтобы 403 возвращался сразу,
       не доходя до IsOrgMember.
"""

class ParsingStatsParsingSwitch:  # существующий класс
    permission_classes = [IsNotDemoUser, IsOrgMember]  # был: [IsOrgMember]


class ParsingStatsUpdateRpm:  # существующий класс
    permission_classes = [IsNotDemoUser, IsOrgMember]  # был: [IsOrgMember]


class VehicleSyncAPIView:  # существующий класс — не имел permission_classes!
    permission_classes = [IsNotDemoUser, IsOrgMember]  # НОВОЕ


class CarDataRequestAPIView:  # существующий класс — не имел permission_classes!
    permission_classes = [IsNotDemoUser, IsOrgMember]  # НОВОЕ


class MileageCalculationAPIView:  # существующий класс — не имел permission_classes!
    permission_classes = [IsNotDemoUser, IsOrgMember]  # НОВОЕ


class MotohoursCalculationAPIView:  # существующий класс — не имел permission_classes!
    permission_classes = [IsNotDemoUser, IsOrgMember]  # НОВОЕ


class StartTerminalMessagesParsingView:  # существующий класс
    permission_classes = [IsNotDemoUser, IsOrgMember]  # был: [IsOrgMember]


class AutoDataListAPIView:  # существующий класс
    permission_classes = [IsNotDemoUser, IsOrgMember]  # был: [IsOrgMember]


class CarSensorsRawDataAPIView:  # существующий класс
    permission_classes = [IsNotDemoUser, IsAuthenticated]  # был: [IsAuthenticated]


class CarLeaksChartsAPIView:  # существующий класс
    permission_classes = [IsNotDemoUser, IsAuthenticated]  # был: [IsAuthenticated]


# ------------------------------------------------------------
# Мутирующие вьюхи — блокируем demo на POST/PUT/PATCH/DELETE
# ------------------------------------------------------------

class CarActiveStatusAPIView:  # существующий класс
    permission_classes = [IsNotDemoUser, IsOrgMember]  # был: [IsOrgMember]


class DataProviderCreateAPIView:  # существующий класс
    permission_classes = [IsNotDemoUser, IsOrgMember]  # был: [IsOrgMember]


class DataProviderDetailAPIView:  # RetrieveUpdateDestroyAPIView
    # GET — разрешён даже для demo через IsOrgMember
    # PUT/PATCH/DELETE — IsNotDemoUser заблокирует
    # Решение: используем IsDemoUser (ограничивает до SAFE_METHODS)
    # вместо IsNotDemoUser (полная блокировка)
    permission_classes = [IsDemoUser, IsOrgMember]  # был: [IsOrgMember]


class UserCarListListView:  # ListCreateAPIView — GET+POST
    permission_classes = [IsDemoUser, IsOrgMember]  # был: [IsOrgMember]


class UserCarListDetailView:  # RetrieveUpdateDestroyAPIView
    permission_classes = [IsDemoUser, IsAuthenticated]  # был: [IsAuthenticated]


class AlertSubscriptionAPIView:  # GET+PATCH
    permission_classes = [IsDemoUser, IsOrgMember]  # был: [IsOrgMember]


class UserInfoAPIView:  # GET+PATCH
    permission_classes = [IsDemoUser, IsOrgMember]  # был: [IsOrgMember]


class UserRegistrationAPIView:  # только POST — полная блокировка
    permission_classes = [IsNotDemoUser]  # был: []


class TelegramRegisterAPIView:  # только POST — полная блокировка
    # authentication_classes = []
    # permission_classes = []  — остаётся как есть (публичный)
    pass  # не меняем


# ------------------------------------------------------------
# Примечание: следующие вьюхи НЕ МЕНЯЮТСЯ (только GET методы):
# ------------------------------------------------------------
# OrganizationListAPIView      — ListAPIView (только GET)
# OrganizationDetailAPIView    — RetrieveAPIView (только GET)
# OrgUserListAPIView           — ListAPIView (только GET)
# OrgUserDetailAPIView         — RetrieveAPIView (только GET)
# CarListAPIView               — ListAPIView (только GET)
# CarDetailAPIView             — RetrieveAPIView (только GET)
# CarUnitListAPIView           — ListAPIView (только GET)
# CarConsumptionListAPIView    — ListAPIView (только GET)
# CarConsumptionDetailAPIView  — RetrieveAPIView (только GET)
# ReportQueryListAPIView       — ListAPIView (только GET)
# ReportQueryDetailAPIView     — RetrieveAPIView (только GET)
# CarReportListAPIView         — ListAPIView (только GET)
# CarReportDetailAPIView       — RetrieveAPIView (только GET)
# CarFuelReportListAPIView     — ListAPIView (только GET)
# CarFuelReportDetailAPIView   — RetrieveAPIView (только GET)
# CarMileageReportListAPIView  — ListAPIView (только GET)
# CarMileageReportDetailAPIView — RetrieveAPIView (только GET)
# DriverListAPIView            — ListAPIView (только GET)
# DriverDetailAPIView          — RetrieveAPIView (только GET)
# DataProviderListAPIView      — ListAPIView (только GET)
# CarLeaksAPIView              — ListAPIView (только GET)
# SensorsKeyListAPIView        — ListAPIView (только GET)
# CarBadDataAPIView            — ListAPIView (только GET)
# CarBadDataDetailAPIView      — RetrieveAPIView (только GET)
# CarBadDataDashboardAPIView   — GET
# LanguageListAPIView          — ListAPIView (только GET)
# TimezoneListAPIView          — GET
# APICalculationLogListAPIView — ListAPIView (только GET)
# APICalculationLogRetrieveAPIView — RetrieveAPIView (только GET)
