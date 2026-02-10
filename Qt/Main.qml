import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Window {
    id: root
    visibility: Window.Maximized
    title: "Consumer"
    visible: true

    ListModel {
        id: deviceModel

        ListElement { devicename: "Pulse"; patientname: "Pilsner Anna"; room: "192"; value: "98 bpm"; alarm:"On";  priority: 3; timeout: 0}
        ListElement { devicename: "SpO2";  patientname: "Müller Simon"; room: "192"; value: "95 %"; alarm:"Off"; priority: 2; timeout: 0}
        ListElement { devicename: "Temp";  patientname: "Horbokon Eugen"; room: "192"; value: "36.6°"; alarm:"Off"; priority: 2; timeout: 0}
    }

    LoginPage {
        id: loginPage
        anchors.fill: parent
        mainPage: mainPage
        visible: false
    }

    MainPage {
        id: mainPage
        anchors.fill: parent
        deviceModel: deviceModel
        devicePage: devicePage
        visible: true
    }

    DevicePage {
        id: devicePage
        anchors.fill: parent
        deviceModel: deviceModel
        mainPage: mainPage
        metricPage: metricPage
        opPage: opPage
        visible: false
    }

    MetricPage {
        id: metricPage
        anchors.fill: parent
        devicePage: devicePage
        mainPage: mainPage
        visible: false
    }

    OperationPage {
        id: opPage
        anchors.fill: parent
        devicePage: devicePage
        mainPage: mainPage
        visible: false
    }
}
