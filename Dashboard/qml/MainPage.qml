import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Item {
    id: mainPage
    property ListModel deviceModel: ListModel {}
    property var devicePage
    property string loginName: ""

    // ── Patient drill-down filter (set by PatientOverview on card click) ───────
    // "" means "show all patients"; set to patientname to show one patient only.
    property string filterPatient: ""

    // Reference back to PatientOverview for the Back button
    property var patientOverview

    // ── Master source-of-truth array — NEVER cleared by filter operations ─────
    // Each element is a plain JS object identical to what was appended to deviceModel.
    property var masterDeviceArray: []

    anchors.fill: parent

    // ─────────────────────────────────────────────────────────────────────────
    // LINK TO PYTHON BACKEND
    // ─────────────────────────────────────────────────────────────────────────
    Connections {
        target: sdcManager

        function onDeviceConnected(device) {
            console.log("QML: New device connected: " + device.patientName + " [" + device.epr + "]")

            var entry = {
                "epr":          device.epr,
                "devicename":   device.deviceName,
                "patientname":  device.patientName,
                "patientid":    device.patientId,    // canonical ID — matches PatientOverviewModel key
                "room":         device.patientRoom,
                "value":        device.deviceValue,
                "alarm":        device.alarmStatus,
                "priority":     device.priority,
                "deviceObj":    device,
                "timeout":      "0"
            }

            // 1. Push into master array (permanent for the session)
            var arr = mainPage.masterDeviceArray
            arr.push(entry)
            mainPage.masterDeviceArray = arr   // reassign to notify QML bindings

            // 2. Rebuild proxy model respecting current filters
            applyFilters()
        }

        function onDeviceDisconnected(epr) {
            console.log("QML: Device disconnected: " + epr)

            // Remove from master array (device truly gone from network)
            mainPage.masterDeviceArray = mainPage.masterDeviceArray.filter(
                function(e) { return e.epr !== epr }
            )

            // Rebuild proxy
            applyFilters()
        }

        // Room filter change — no longer destructive; just re-applies filters
        function onRoomChanged(room) {
            console.log("QML: Room switched to: '" + room + "'")
            applyFilters()
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // applyFilters() — filter + sort in a single pass; ONE clear() per call
    // ─────────────────────────────────────────────────────────────────────────
    function applyFilters() {
        var activeRoom    = (sdcManager && sdcManager.currentRoom) ? sdcManager.currentRoom : ""
        var activePatient = mainPage.filterPatient

        // Step 1: filter masterDeviceArray into a local JS array.
        //   Topology fields (room, patientid, patientname) are refreshed from the
        //   live deviceObj BEFORE filter evaluation — fixes async MDIB init race
        //   where patientRoom arrives after onDeviceConnected snapshots e.room = "".
        var filtered = []
        for (var i = 0; i < mainPage.masterDeviceArray.length; i++) {
            var e = mainPage.masterDeviceArray[i]

            // Sync ALL fields from the live C++/Python object before any comparison.
            if (e.deviceObj) {
                e.room        = e.deviceObj.patientRoom   // topology — may have been "" at connect
                e.patientid   = e.deviceObj.patientId     // topology
                e.patientname = e.deviceObj.patientName   // topology
                e.alarm       = e.deviceObj.alarmStatus   // clinical
                e.value       = e.deviceObj.deviceValue   // clinical
                e.priority    = e.deviceObj.priority      // clinical
            }

            var roomMatch    = (activeRoom    === "") || (e.room        === activeRoom)
            // Accept both patientId (MDIB Extension) and patientName (CoreData) — PatientOverview
            // may pass either depending on which field is available for the patient.
            var patientMatch = (activePatient === "") || (e.patientname === activePatient)
                                                     || (e.patientid   === activePatient)

            if (roomMatch && patientMatch) {
                filtered.push(e)
            }
        }

        // Step 2: sort the local array (same rank logic as sortByPriority)
        filtered.sort(function(a, b) {
            var r = _alarmRank(b.alarm) - _alarmRank(a.alarm)
            if (r !== 0) return r
            return parseInt(b.priority) - parseInt(a.priority)
        })

        // Step 3: ONE clear() + N append() — avoids double-rebuild on navigation
        deviceModel.clear()
        for (var j = 0; j < filtered.length; j++) {
            deviceModel.append(filtered[j])
        }
    }

    // Shared rank function used by both applyFilters and sortByPriority
    function _alarmRank(status) {
        if (status === "On")           return 6
        if (status === "Warning")      return 5
        if (status === "COMM_FAILURE") return 4
        if (status === "Ack")          return 3
        if (status === "Latch")        return 2
        return 1
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Sorting (unchanged logic, now called from applyFilters)
    // ─────────────────────────────────────────────────────────────────────────
    Timer {
        id: sortTimer
        interval: 100
        repeat: false
        onTriggered: sortByPriority()
    }

    function sortByPriority() {
        let items = []
        for (let i = 0; i < deviceModel.count; i++) {
            let item = deviceModel.get(i)
            items.push({
                "epr":          item.epr,
                "devicename":   item.devicename,
                "patientname":  item.patientname,
                "patientid":    item.patientid,
                "room":         item.room,
                "value":        item.value,
                "alarm":        item.alarm,
                "priority":     item.priority,
                "deviceObj":    item.deviceObj,
                "timeout":      item.timeout
            })
        }

        items.sort((a, b) => {
            // Bayesian statuses (On, Warning) take absolute priority over
            // network/operator statuses (Ack, Latch).
            let rankA = _alarmRank(a.alarm)
            let rankB = _alarmRank(b.alarm)
            if (rankA !== rankB) return rankB - rankA
            return parseInt(b.priority) - parseInt(a.priority)
        })

        deviceModel.clear()
        for (let item of items) deviceModel.append(item)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // TOP BAR
    // ─────────────────────────────────────────────────────────────────────────
    Rectangle {
        width: parent.width
        height: parent.height * 0.15
        color: "#515c80"

        // ── Back button (visible only in drill-down mode) ──────────────────
        Button {
            id: backButton
            visible: mainPage.filterPatient !== ""
            text: "← Patients"
            width: 140
            height: 36
            anchors.left: parent.left
            anchors.leftMargin: 20
            anchors.verticalCenter: parent.verticalCenter

            background: Rectangle {
                radius: 6
                color: "#3a5f9c"
                border.color: "#9ab0d9"
                border.width: 2
            }
            contentItem: Text {
                text: backButton.text
                color: "white"
                font.pixelSize: 15
                font.family: "Tahoma"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            onClicked: {
                mainPage.filterPatient = ""   // clear patient filter first
                applyFilters()                // rebuild proxy with no patient filter
                mainPage.visible = false
                if (mainPage.patientOverview)
                    mainPage.patientOverview.visible = true
            }
        }

        // Title — shifts right when back button visible
        Text {
            anchors.left: backButton.visible ? backButton.right : parent.left
            anchors.leftMargin: backButton.visible ? 20 : 25
            anchors.verticalCenter: parent.verticalCenter
            text: mainPage.filterPatient !== ""
                  ? loginName + "Devices"
                  : loginName + "Patients (Devices)"
            color: "white"
            font.pixelSize: 32
            font.family: "Tahoma"
        }

        // Room switcher moved to PatientOverview.qml (topBar)

        Popup {
            id: pop
            modal: true
            focus: true
            width: 300
            height: 160
            x: (mainPage.width - width) / 2
            y: (mainPage.height - height) / 2

            background: Rectangle { color: "#515c80"; radius: 10 }

            Text {
                id: msg
                text: "Operation completed!"
                color: "white"
                font.pixelSize: 22
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: 30
            }
            Button {
                text: "OK"
                width: 120
                height: 40
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: msg.bottom
                anchors.topMargin: 30
                onClicked: pop.close()
            }
        }

        Button {
            id: myButton
            anchors.right: parent.right
            anchors.rightMargin: 230
            anchors.verticalCenter: parent.verticalCenter
            text: "Call Help"
            background: Rectangle {
                radius: 8
                gradient: gradOff2
                Gradient {
                    id: gradOff2
                    GradientStop { position: 0.0; color: "#5e6ea5" }
                    GradientStop { position: 1.0; color: "#7487c4" }
                }
            }
            contentItem: Text {
                text: myButton.text
                color: "white"
                font.pixelSize: 22
                font.family: "Tahoma"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            onClicked: pop.open()
        }

        Button {
            id: myButton2
            anchors.right: parent.right
            anchors.rightMargin: 400
            anchors.verticalCenter: parent.verticalCenter
            text: "Pause"
            background: Rectangle {
                radius: 8
                gradient: gradOff3
                Gradient {
                    id: gradOff3
                    GradientStop { position: 0.0; color: "#5e6ea5" }
                    GradientStop { position: 1.0; color: "#7487c4" }
                }
            }
            contentItem: Text {
                text: myButton2.text
                color: "white"
                font.pixelSize: 22
                font.family: "Tahoma"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            onClicked: pop.open()
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // CONTENT AREA
    // ─────────────────────────────────────────────────────────────────────────
    Rectangle {
        id: contentArea
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: parent.height * 0.15
        anchors.left: parent.left
        anchors.right: parent.right
        color: "#191d2c"
        clip: true

        Flickable {
            id: flick
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: parent.height * 0.05
            anchors.bottomMargin: parent.height * 0.05
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: parent.width * 0.02
            anchors.rightMargin: parent.width * 0.02
            contentWidth: width
            contentHeight: column.height
            clip: true

            onMovementStarted: { scrollBar.opacity = 1; hideTimer.restart() }
            onMovementEnded: hideTimer.restart()

            Column {
                id: column
                width: flick.width
                spacing: 16

                Repeater {
                    model: deviceModel
                    delegate: Rectangle {
                        id: delegateButton

                        Connections {
                            target: model.deviceObj
                            function onDeviceValueChanged()  { model.value       = target.deviceValue }
                            function onPatientNameChanged()  { model.patientname = target.patientName }
                            function onPatientIdChanged()    { model.patientid   = target.patientId   }
                            function onPatientRoomChanged()  { model.room        = target.patientRoom }
                            function onDeviceNameChanged()   { model.devicename  = target.deviceName  }
                            function onAlarmStatusChanged()  { model.alarm       = target.alarmStatus; sortTimer.restart() }
                            function onPriorityChanged()     { model.priority    = target.priority;    sortTimer.restart() }
                        }

                        width: column.width
                        height: 80
                        radius: 20

                        color: (model.alarm === "On" || model.alarm === "COMM_FAILURE" || model.alarm === "Warning")
                               ? "transparent" : "#5e6ea5"

                        gradient: model.alarm === "On"            ? gradOn
                                : (model.alarm === "Warning"      ? gradWarn
                                : (model.alarm === "COMM_FAILURE" ? gradComm
                                : (model.alarm === "Ack"          ? gradAck
                                : (model.alarm === "Latch"        ? gradLatch : gradOff))))

                        // On/Warning/COMM_FAILURE: use gradient, rest use flat color fallback
                        Gradient { id: gradOn;    GradientStop { position: 0.0; color: "#8c2f2f" } GradientStop { position: 1.0; color: "#af3c3c" } }
                        Gradient { id: gradWarn;  GradientStop { position: 0.0; color: "#8a7010" } GradientStop { position: 1.0; color: "#bfa11f" } }
                        Gradient { id: gradComm;  GradientStop { position: 0.0; color: "#4a3030" } GradientStop { position: 1.0; color: "#7a3030" } }
                        Gradient { id: gradAck;   GradientStop { position: 0.0; color: "#2e4a6e" } GradientStop { position: 1.0; color: "#3d5f8a" } }
                        Gradient { id: gradLatch; GradientStop { position: 0.0; color: "#303d60" } GradientStop { position: 1.0; color: "#3e4f7a" } }
                        Gradient { id: gradOff;   GradientStop { position: 0.0; color: "#5e6ea5" } GradientStop { position: 1.0; color: "#7487c4" } }

                        SequentialAnimation on opacity {
                            // Blink ONLY for hard clinical alerts: Escalated (On) and COMM_FAILURE.
                            // Warning is static yellow — risk is accumulating, not yet critical.
                            // Ack/Latch are intentionally calm — operator already aware.
                            running: model.alarm === "On" || model.alarm === "COMM_FAILURE"
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.5; duration: 500; easing.type: Easing.InOutQuad }
                            NumberAnimation { to: 1.0; duration: 500; easing.type: Easing.InOutQuad }
                            onRunningChanged: { if (!running) delegateButton.opacity = 1.0 }
                        }

                        Text {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 20
                            font.pixelSize: 24
                            font.family: "Tahoma"
                            text: "Room: "       + (model.room       ? model.room       : "?")       + " | " +
                                  (model.devicename  ? model.devicename  : "Unknown") + " | " +
                                  (model.value       ? model.value       : "---")     + " | " +
                                  (model.patientname ? model.patientname : "Unknown")
                            color: "white"
                        }

                        Text {
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.rightMargin: 20
                            font.pixelSize: 24
                            font.family: "Tahoma"
                            text: "Priority: " + model.priority
                            color: "white"
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                if (mainPage.devicePage) {
                                    mainPage.devicePage.setDevice(model.deviceObj)
                                    mainPage.visible = false
                                    mainPage.devicePage.visible = true
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // SCROLLBAR
    // ─────────────────────────────────────────────────────────────────────────
    Rectangle {
        id: scrollBar
        width: 4
        anchors.top: contentArea.top
        anchors.topMargin: 20
        anchors.bottom: contentArea.bottom
        anchors.bottomMargin: 20
        anchors.right: parent.right
        anchors.rightMargin: 10
        color: "#384163"
        radius: 4
        opacity: 0

        Behavior on opacity { NumberAnimation { duration: 200 } }

        Rectangle {
            id: handle
            width: parent.width
            height: Math.max(40, parent.height * (flick.height / Math.max(flick.contentHeight, 1)))
            y: Math.max(0, Math.min(
                   flick.contentY / Math.max(flick.contentHeight, 1) * (scrollBar.height - height),
                   scrollBar.height - height))
            color: "#7c7c7c"
            radius: 4

            MouseArea {
                anchors.fill: parent
                drag.target: parent
                drag.axis: Drag.YAxis
                drag.minimumY: 0
                drag.maximumY: scrollBar.height - handle.height
                onPositionChanged: {
                    flick.contentY = handle.y / (scrollBar.height - handle.height) * flick.contentHeight
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