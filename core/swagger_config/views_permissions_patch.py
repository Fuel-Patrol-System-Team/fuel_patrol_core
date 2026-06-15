class ParsingStatsParsingSwitch:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class ParsingStatsUpdateRpm:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class VehicleSyncAPIView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class CarDataRequestAPIView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class MileageCalculationAPIView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class MotohoursCalculationAPIView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class StartTerminalMessagesParsingView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class AutoDataListAPIView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class CarSensorsRawDataAPIView:
    permission_classes = [IsNotDemoUser, IsAuthenticated]


class CarLeaksChartsAPIView:
    permission_classes = [IsNotDemoUser, IsAuthenticated]


class CarActiveStatusAPIView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class DataProviderCreateAPIView:
    permission_classes = [IsNotDemoUser, IsOrgMember]


class DataProviderDetailAPIView:
    permission_classes = [IsDemoUser, IsOrgMember]


class UserCarListListView:
    permission_classes = [IsDemoUser, IsOrgMember]


class UserCarListDetailView:
    permission_classes = [IsDemoUser, IsAuthenticated]


class AlertSubscriptionAPIView:
    permission_classes = [IsDemoUser, IsOrgMember]


class UserInfoAPIView:
    permission_classes = [IsDemoUser, IsOrgMember]


class UserRegistrationAPIView:
    permission_classes = [IsNotDemoUser]


class TelegramRegisterAPIView:
    pass

