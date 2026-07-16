import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Window {
    id: root
    visibility: Window.Maximized
    title: "Consumer"
    visible: true

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
