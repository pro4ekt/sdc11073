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

    // ── NEW: Patient-level overview — default landing screen ──────────────────
    PatientOverview {
        id: patientOverview
        anchors.fill: parent
        mainPage: mainPage
        visible: true
    }

    MainPage {
        id: mainPage
        anchors.fill: parent
        deviceModel: deviceModel
        devicePage: devicePage
        patientOverview: patientOverview   // back-reference for "← Patients" button
        visible: false                     // now hidden until drill-down
    }

    // Shared ListModel — owned here so both PatientOverview and MainPage can reach it
    ListModel { id: deviceModel }

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
