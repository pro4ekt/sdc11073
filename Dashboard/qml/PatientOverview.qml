import QtQuick
import QtQuick.Controls

Item {
    id: patientOverview

    // Set by Main.qml — used for drill-down navigation
    property var mainPage
    property var loginPage

    anchors.fill: parent

    // ─── TOP BAR ──────────────────────────────────────────────────────────────
    Rectangle {
        id: topBar
        width: parent.width
        height: parent.height * 0.15
        color: "#515c80"

        Text {
            anchors.left: parent.left
            anchors.leftMargin: 25
            anchors.verticalCenter: parent.verticalCenter
            text: "Patient Overview"
            color: "white"
            font.pixelSize: 32
            font.family: "Tahoma"
        }

        // Patient count badge
        Rectangle {
            anchors.right: parent.right
            anchors.rightMargin: 30
            anchors.verticalCenter: parent.verticalCenter
            width: 140
            height: 40
            radius: 8
            color: "#404c6e"

            Text {
                anchors.centerIn: parent
                text: "Patients: " + patientRepeater.count
                color: "#ccd0e8"
                font.pixelSize: 17
                font.family: "Tahoma"
            }
        }
    }

    // ─── CONTENT AREA ─────────────────────────────────────────────────────────
    Rectangle {
        id: contentArea
        anchors.top: topBar.bottom
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        color: "#191d2c"
        clip: true

        // Empty-state label
        Text {
            anchors.centerIn: parent
            visible: patientRepeater.count === 0
            text: "Waiting for devices…"
            color: "#4a5272"
            font.pixelSize: 26
            font.family: "Tahoma"
        }

        Flickable {
            id: flick
            anchors.fill: parent
            anchors.margins: 20
            contentWidth: width
            contentHeight: grid.implicitHeight + 20
            clip: true

            onMovementStarted: { scrollBar.opacity = 1; hideTimer.restart() }
            onMovementEnded:   hideTimer.restart()

            // ── Responsive grid of patient cards ──────────────────────────────
            Flow {
                id: grid
                width: flick.width
                spacing: 20

                Repeater {
                    id: patientRepeater
                    // patientOverview context property is the PatientOverviewModel.patients list
                    model: (typeof patientOverview_model !== "undefined") ? patientOverview_model.patients : []

                    delegate: Rectangle {
                        id: card
                        required property var modelData

                        width:  Math.min(flick.width, 520)
                        height: 130
                        radius: 18

                        // Base colour: red when escalated, blue otherwise
                        color: modelData.isEscalated ? "transparent" : "#5e6ea5"

                        gradient: modelData.isEscalated ? gradEscalated : gradNormal

                        Gradient {
                            id: gradEscalated
                            GradientStop { position: 0.0; color: "#8c2f2f" }
                            GradientStop { position: 1.0; color: "#af3c3c" }
                        }
                        Gradient {
                            id: gradNormal
                            GradientStop { position: 0.0; color: "#5e6ea5" }
                            GradientStop { position: 1.0; color: "#7487c4" }
                        }

                        // ── Escalation border glow ─────────────────────────────
                        Rectangle {
                            id: escalationBorder
                            anchors.fill: parent
                            radius: parent.radius
                            color: "transparent"
                            border.color: "#ff4444"
                            border.width: modelData.isEscalated ? 3 : 0

                            // Blink the border opacity — GPU-composited, 60 FPS safe
                            SequentialAnimation on opacity {
                                running: modelData.isEscalated
                                loops:   Animation.Infinite

                                NumberAnimation { to: 0.25; duration: 400; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 1.0;  duration: 400; easing.type: Easing.InOutSine }

                                onRunningChanged: {
                                    if (!running) escalationBorder.opacity = 1.0
                                }
                            }
                        }

                        // ── Status indicator dot ───────────────────────────────
                        Rectangle {
                            id: statusDot
                            width: 18
                            height: 18
                            radius: 9
                            anchors.top: parent.top
                            anchors.topMargin: 14
                            anchors.right: parent.right
                            anchors.rightMargin: 18
                            color: modelData.isEscalated ? "#af3c3c" : "#4caf50"

                            // Pulse the dot when escalated
                            SequentialAnimation on scale {
                                running: modelData.isEscalated
                                loops:   Animation.Infinite
                                NumberAnimation { to: 1.35; duration: 400; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 1.0;  duration: 400; easing.type: Easing.InOutSine }
                                onRunningChanged: { if (!running) statusDot.scale = 1.0 }
                            }
                        }

                        // ── Patient info ───────────────────────────────────────
                        Column {
                            anchors.left:  parent.left
                            anchors.leftMargin: 22
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 8

                            Text {
                                text: modelData.patientName !== "" ? modelData.patientName : "Unknown Patient"
                                color: "white"
                                font.pixelSize: 24
                                font.family: "Tahoma"
                                font.bold: true
                            }

                            Row {
                                spacing: 20

                                Text {
                                    text: "Room: " + (modelData.room !== "" ? modelData.room : "—")
                                    color: "#ccd0e8"
                                    font.pixelSize: 17
                                    font.family: "Tahoma"
                                }

                                Text {
                                    text: "Devices: " + modelData.deviceCount
                                    color: "#ccd0e8"
                                    font.pixelSize: 17
                                    font.family: "Tahoma"
                                }

                                Text {
                                    visible: modelData.isEscalated
                                    text: "Risk: " + modelData.riskScore.toFixed(1)
                                    color: "#ffaaaa"
                                    font.pixelSize: 17
                                    font.family: "Tahoma"
                                    font.bold: true
                                }
                            }

                            Text {
                                visible: modelData.isEscalated
                                text: "⚠ ESCALATED — Clinical alarm confirmed"
                                color: "#ffcccc"
                                font.pixelSize: 14
                                font.family: "Tahoma"
                            }
                        }

                        // ── Drill-down click ───────────────────────────────────
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                if (patientOverview.mainPage) {
                                    // Use patientName as the foreign key — this matches
                                    // entry.patientname stored in masterDeviceArray
                                    patientOverview.mainPage.filterPatient = modelData.patientName
                                    patientOverview.mainPage.applyFilters()   // rebuild before showing
                                    patientOverview.visible = false
                                    patientOverview.mainPage.visible = true
                                }
                            }
                        }
                    }
                }
            }
        }

        // ── Scrollbar ──────────────────────────────────────────────────────────
        Rectangle {
            id: scrollBar
            width: 4
            anchors.top: parent.top
            anchors.topMargin: 20
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 20
            anchors.right: parent.right
            anchors.rightMargin: 6
            color: "#384163"
            radius: 4
            opacity: 0
            Behavior on opacity { NumberAnimation { duration: 200 } }

            Rectangle {
                id: sbHandle
                width: parent.width
                height: Math.max(40, parent.height * (flick.height / Math.max(flick.contentHeight, 1)))
                y: Math.max(0, Math.min(
                       flick.contentY / Math.max(flick.contentHeight - flick.height, 1) * (scrollBar.height - height),
                       scrollBar.height - height))
                color: "#7c7c7c"
                radius: 4

                MouseArea {
                    anchors.fill: parent
                    drag.target: parent
                    drag.axis: Drag.YAxis
                    drag.minimumY: 0
                    drag.maximumY: scrollBar.height - sbHandle.height
                    onPositionChanged: {
                        flick.contentY = sbHandle.y / Math.max(scrollBar.height - sbHandle.height, 1) * flick.contentHeight
                    }
                }
            }
        }

        Timer {
            id: hideTimer
            interval: 800
            repeat: false
            onTriggered: scrollBar.opacity = 0
        }
    }
}

