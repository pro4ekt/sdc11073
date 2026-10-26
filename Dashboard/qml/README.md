# Dashboard/qml — user interface

QML files loaded by `main.py` through `QQmlApplicationEngine`; `Main.qml` is the root window
and switches between the pages.

- `LoginPage.qml` — start page.
- `PatientOverview.qml` — one card per patient ensemble (backed by `Dashboard/app/patientOverviewModel.py`).
- `MainPage.qml` — list of discovered devices and their state.
- `DevicePage.qml` — one device: metrics, alarms, acknowledge button (`silenceAlarm`).
- `MetricPage.qml` — metric details; `OperationPage.qml` — OR session page.

The pages talk to Python through the context properties `sdcManager` (`SdcMyConsumer`),
`patientOverview_model` and one `QtDeviceHandler` per device. Images come from `../img/`.
