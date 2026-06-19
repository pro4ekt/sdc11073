import QtQuick
import QtQuick.Controls
import QtQuick.Effects
//import Qt5Compat.GraphicalEffects

Item {
    id: devicePage
    // Store the current Python Device Object
    property var currentDevice: null

    property var deviceModel
    property var mainPage
    property var metricPage
    property var opPage

    property string devicename: ""
    property string patientname: ""
    property string room: ""
    property string value: ""
    property string alarm: ""
    property int priority: 0
    property int timeout: 0

    // Setup function now expects the QtDeviceHandler object
    function setDevice(deviceObj) {
        var isNewDevice = (currentDevice !== deviceObj)
        currentDevice = deviceObj

        // Pass the device object to the Operation Page too, so it can bind to signals
        if (opPage && typeof opPage.setDevice === "function") {
            opPage.setDevice(deviceObj)
        }

        // Bind local properties to the Python object's properties
        devicename = deviceObj.epr
        patientname = deviceObj.patientName
        room = deviceObj.patientRoom
        value = deviceObj.deviceValue
        alarm = deviceObj.alarmStatus
        priority = parseInt(deviceObj.priority) || 0
        // Reset timeout only when switching to a DIFFERENT device —
        // preserves the local bell-silence state when re-entering the same device page.
        if (isNewDevice) {
            timeout = 0
        }

        // Force refresh the list of metrics
        refreshMetrics()
        refreshOperations()
    }

    function refreshMetrics() {
        metricListModel.clear()

        if (currentDevice && currentDevice.metrics) {
            // currentDevice.metrics comes from @Property(list) in main.py
            // 1. Copy to JS array for sorting
            var metricsArray = []
            var sourceList = currentDevice.metrics
            for (var i = 0; i < sourceList.length; i++) {
                metricsArray.push(sourceList[i])
            }

            // 2. Sort Logic: Alarm 'On' > Alarm 'Off'
            metricsArray.sort(function(a, b) {
                var alarmA = (a.alarm === "On")
                var alarmB = (b.alarm === "On")

                if (alarmA && !alarmB) return -1
                if (!alarmA && alarmB) return 1
                return 0 // Keep original order if statuses match
            })

            // 3. Populate Model
            for (var j = 0; j < metricsArray.length; j++) {
                var m = metricsArray[j]
                metricListModel.append({
                    "metricname": m.metricname, // descriptor handle
                    "value": m.value,
                    "alarm": m.alarm,
                    "timeout": 0,
                    "metricObj": m // Store the actual data object (fixes index mismatch when sorted)
                })
            }
        }
    }

    function refreshOperations() {
        opListModel.clear()
        if (currentDevice && currentDevice.operations) {
            var ops = currentDevice.operations
            for (var i = 0; i < ops.length; i++) {
                opListModel.append({
                    "opname": ops[i].name,
                    "alarm": "Off", // Operations typically don't have "Alarms", defaults to Blue/Off
                    "timeout": 0
                })
            }
        }
    }

    // Listen for live updates from the specific device object
    Connections {
        target: currentDevice
        ignoreUnknownSignals: true

        function onMetricsChanged() {
            refreshMetrics()
        }

        function onDeviceValueChanged() {
            devicePage.value = currentDevice.deviceValue
        }

        function onPatientNameChanged() {
            devicePage.patientname = currentDevice.patientName
        }

        function onAlarmStatusChanged() {
            devicePage.alarm = currentDevice.alarmStatus
        }

        function onOperationsChanged() {
            refreshOperations()
        }
    }

    anchors.fill: parent

    // ---------------- TOP BAR ----------------
    Rectangle {
        width: parent.width
        height: parent.height * 0.15
        anchors.top: parent.top
        color: "#515c80"

        Text {
            anchors.left: parent.left
            anchors.leftMargin: parent.width * 0.15
            anchors.verticalCenter: parent.verticalCenter
            text: patientname
            color: "white"
            font.pixelSize: 32
            font.family: "Tahoma"
        }

        /*
        Image {
            width: 40
            height: 40
            anchors.right: parent.right
            anchors.rightMargin: parent.width * 0.02
            anchors.verticalCenter: parent.verticalCenter
            source: "img/login.jpg"

            MouseArea {
                anchors.fill: parent
                onPressed: alarmPopup.open()
            }
        }
        */

        // ---- Bell icon with Ack (yellow) colour support ----
        Item {
            width: parent.width * 0.1
            height: parent.height

            Image {
                id: imageInstance
                anchors.fill: parent

                source: (alarm === "On" && timeout === 1) || alarm === "Ack"
                        ? "img/noSound.png"
                        : ((alarm === "On" && timeout === 0)
                            ? "img/bellOn.png"
                            : "img/bellOff.png")

                fillMode: Image.PreserveAspectCrop
                layer.enabled: true
            }

            // Yellow tint overlay — visible when Ack (from backend) OR locally silenced (timeout=1)
            Rectangle {
                anchors.fill: parent
                color: "#FFD700"
                opacity: 0.38
                visible: alarm === "Ack" || (alarm === "On" && timeout === 1)
            }

            MouseArea {
                anchors.fill: parent
                onPressed: {
                    if (alarm === "On" || alarm === "Ack") {
                        timeout = timeout === 0 ? 1 : 0
                    }
                }
            }
        }

        Image {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 150
            height: 150
            source: "img/homepage.png"

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    devicePage.visible = false
                    mainPage.visible = true
                }
            }
        }

        Image {
            anchors.right: parent.right
            anchors.rightMargin: 120
            anchors.verticalCenter: parent.verticalCenter
            width: 80
            height: 140
            source: "img/arrow.png"

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    devicePage.visible = false
                    mainPage.visible = true
                }
            }
        }

        Popup {
            id: pop
            modal: true
            focus: true
            width: 300
            height: 160

            // Центрирование по mainPage
            x: (mainPage.width - width) / 2
            y: (mainPage.height - height) / 2

            background: Rectangle {
                color: "#515c80"
                radius: 10
            }

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
                id: okBtn
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
            onClicked: {
                    pop.open()
                }
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
            onClicked: {
                    pop.open()
                }
        }

    }

    // ---------------- METRICS BLOCK ----------------
    Rectangle {
        id: metricsBlock
        width: parent.width * 0.3
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: parent.height * 0.15
        color: "#191d2c"
        clip: true

        Rectangle {
            width: parent.width
            height: 50
            anchors.top: parent.top
            color: "#191d2c"

            Text {
                text: "Vitals"
                color: "white"
                font.family: "Tahoma"
                anchors.top: parent.top
                anchors.topMargin: 15
                anchors.horizontalCenter: parent.horizontalCenter
                font.pixelSize: 32
            }

        }

        Rectangle {
            id: metricsContent
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 50
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: parent.width * 0.02
            anchors.rightMargin: parent.width * 0.02
            clip: true
            color: "#191d2c"

            ListModel {
                id: metricListModel
                // Removed hardcoded elements, now populated via setDevice
            }

            Flickable {
                id: flickMetrics
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.topMargin: parent.height * 0.05
                anchors.bottomMargin: parent.height*0.05
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: parent.width * 0.02
                anchors.rightMargin: parent.width * 0.02
                contentWidth: width
                contentHeight: metricsColumn.height
                clip: true

                onMovementStarted: {
                    scrollBarMetrics.opacity = 1
                    hideTimerMetrics.restart()
                }
                onMovementEnded: hideTimerMetrics.restart()

                Column {
                    id: metricsColumn
                    width: flickMetrics.width
                    spacing: 16

                    Repeater {
                        model: metricListModel
                        //Елементы в метриках
                        delegate: Rectangle {
                            width: metricsColumn.width
                            height: metricsColumn.width * 0.2
                            radius: 10

                            // Выбираем градиент в зависимости от Alarm
                            gradient: alarm === "On" ? gradOn : gradOff

                            //Градиент для Alarm On
                            Gradient {
                                id: gradOn
                                GradientStop { position: 0.0; color: "#8c2f2f" }
                                GradientStop { position: 1.0; color: "#af3c3c" }
                            }

                            //Градиент для Alarm Off
                            Gradient {
                                id: gradOff
                                GradientStop { position: 0.0; color: "#5e6ea5" }
                                GradientStop { position: 1.0; color: "#7487c4" }
                            }

                            // Мигаем только если Alarm On
                            SequentialAnimation on opacity {
                                running: alarm === "On"
                                loops: Animation.Infinite

                                NumberAnimation { to: 0.5; duration: 500; easing.type: Easing.InOutQuad }
                                NumberAnimation { to: 1.0; duration: 500; easing.type: Easing.InOutQuad }
                            }
                            //текст в Метриках
                            Text {
                                id: textItem
                                anchors.centerIn: parent

                                text: metricname
                                color: "white"

                                font.pixelSize: 20
                                wrapMode: Text.NoWrap
                                elide: Text.ElideRight
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    // CHANGED: Use the stored sorted object instead of index
                                    // 'index' refers to UI row, which doesn't match backend list if sorted.
                                    var metricData = model.metricObj

                                    metricPage.setMetric(metricData)

                                    devicePage.visible = false
                                    metricPage.visible = true
                                }
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: scrollBarMetrics
            width: 4
            anchors.top: metricsContent.top
            anchors.bottom: metricsContent.bottom
            anchors.topMargin: 15
            anchors.bottomMargin: 15
            anchors.right: parent.right
            color: "#384163"
            radius: 4
            opacity: 0

            Behavior on opacity { NumberAnimation { duration: 200 } }

            Rectangle {
                id: handleMetrics
                width: parent.width
                height: Math.max(40, parent.height * (flickMetrics.height / flickMetrics.contentHeight))

                y: Math.max(0, Math.min(
                        flickMetrics.contentY / flickMetrics.contentHeight * (scrollBarMetrics.height - height),
                        scrollBarMetrics.height - height
                    ))

                color: "#7c7c7c"
                radius: 4

                MouseArea {
                    anchors.fill: parent
                    drag.target: parent
                    drag.axis: Drag.YAxis
                    drag.minimumY: 0
                    drag.maximumY: scrollBarMetrics.height - handleMetrics.height

                    onPositionChanged: {
                        flickMetrics.contentY =
                            handleMetrics.y / (scrollBarMetrics.height - handleMetrics.height) * flickMetrics.contentHeight
                    }
                }
            }
        }

        Timer {
            id: hideTimerMetrics
            interval: 800
            repeat: false
            onTriggered: scrollBarMetrics.opacity = 0
        }
    }

    // ---------------- REMOTE OPERATIONS BLOCK ----------------
    Rectangle {
        id: remoteBlock
        width: parent.width * 0.3
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.leftMargin: parent.width * 0.3
        anchors.topMargin: parent.height * 0.15
        color: "#191d2c"
        clip: true

        Rectangle {
            width: parent.width
            height: 50
            anchors.top: parent.top
            color: "#191d2c"

            Text {
                text: "Remote Control"
                anchors.top: parent.top
                anchors.topMargin: 15
                anchors.horizontalCenter: parent.horizontalCenter
                font.pixelSize: 32
                font.family: "Tahoma"
                color: "white"
            }
        }

        Rectangle {
            id: remoteContent
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 50
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: parent.width * 0.02
            anchors.rightMargin: parent.width * 0.02
            clip: true
            color: "#191d2c"

            ListModel {
                id: opListModel
                // Removed hardcoded elements, now populated via refreshOperations
            }

            Flickable {
                id: flickOps
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.topMargin: parent.height * 0.05
                anchors.bottomMargin: parent.height*0.05
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: parent.width * 0.02
                anchors.rightMargin: parent.width * 0.02
                contentWidth: width
                contentHeight: opsColumn.height
                clip: true

                onMovementStarted: {
                    scrollBarOps.opacity = 1
                    hideTimerOps.restart()
                }
                onMovementEnded: hideTimerOps.restart()

                Column {
                    id: opsColumn
                    width: flickOps.width
                    spacing: 16

                    Repeater {
                        model: opListModel

                        delegate: Rectangle {
                            width: opsColumn.width
                            height: opsColumn.width * 0.2
                            radius: 10

                            // Выбираем градиент в зависимости от Alarm
                            gradient: alarm === "On" ? gradOnOp : gradOffOp

                            //Градиент для Alarm On
                            Gradient {
                                id: gradOnOp
                                GradientStop { position: 0.0; color: "#8c2f2f" }
                                GradientStop { position: 1.0; color: "#af3c3c" }
                            }

                            //Градиент для Alarm Off
                            Gradient {
                                id: gradOffOp
                                GradientStop { position: 0.0; color: "#5e6ea5" }
                                GradientStop { position: 1.0; color: "#7487c4" }
                            }

                            // Мигаем только если Alarm On
                            SequentialAnimation on opacity {
                                running: alarm === "On"
                                loops: Animation.Infinite

                                NumberAnimation { to: 0.5; duration: 500; easing.type: Easing.InOutQuad }
                                NumberAnimation { to: 1.0; duration: 500; easing.type: Easing.InOutQuad }
                            }


                            Text {
                                id: textItem2
                                anchors.centerIn: parent
                                text: opname
                                font.family: "Tahoma"
                                color: "white"
                                font.pixelSize: 20
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    opPage.setOp({
                                    opname: opname,
                                    timeout: timeout,
                                    alarm: alarm
                                    })

                                    devicePage.visible = false
                                    opPage.visible = true
                                }
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: scrollBarOps
            width: 4
            anchors.top: remoteContent.top
            anchors.topMargin: 15
            anchors.bottom: remoteContent.bottom
            anchors.bottomMargin: 15
            anchors.right: parent.right
            color: "#384163"
            radius: 4
            opacity: 0

            Behavior on opacity { NumberAnimation { duration: 200 } }

            Rectangle {
                id: handleOps
                width: parent.width
                height: Math.max(40, parent.height * (flickOps.height / flickOps.contentHeight))

                y: Math.max(0, Math.min(
                        flickOps.contentY / flickOps.contentHeight * (scrollBarOps.height - height),
                        scrollBarOps.height - height
                    ))

                color: "#7c7c7c"
                radius: 4

                MouseArea {
                    anchors.fill: parent
                    drag.target: parent
                    drag.axis: Drag.YAxis
                    drag.minimumY: 0
                    drag.maximumY: scrollBarOps.height - handleOps.height

                    onPositionChanged: {
                        flickOps.contentY =
                            handleOps.y / (scrollBarOps.height - handleOps.height) * flickOps.contentHeight
                    }
                }
            }
        }

        Timer {
            id: hideTimerOps
            interval: 800
            repeat: false
            onTriggered: scrollBarOps.opacity = 0
        }
    }

    // ---------------- THIRD BLOCK ----------------
    Rectangle {
        width: parent.width * 0.4
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.leftMargin: parent.width * 0.6
        anchors.topMargin: parent.height * 0.15
        color: "#191d2c"
        clip: true

        Rectangle {
            width: parent.width
            height: 50
            anchors.top: parent.top
            color: "#191d2c"

            Text {
                text: "Doctor's Report"
                anchors.top: parent.top
                anchors.topMargin: 15
                anchors.horizontalCenter: parent.horizontalCenter
                font.pixelSize: 32
                color: "white"
                font.family: "Tahoma"
            }
        }

        Rectangle {
            id: notesContent
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 50
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: parent.width * 0.02
            anchors.rightMargin: parent.width * 0.02
            clip: true
            color: "#191d2c"

            Flickable {
                id: flickNotes
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.topMargin: parent.height * 0.05
                anchors.bottomMargin: parent.height*0.05
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: parent.width * 0.02
                anchors.rightMargin: parent.width * 0.02
                contentWidth: width
                contentHeight: notesColumn.height
                clip: true

                onMovementStarted: {
                    scrollBarNotes.opacity = 1
                    hideTimerNotes.restart()
                }
                onMovementEnded: hideTimerNotes.restart()

                Column {
                    id: notesColumn
                    width: flickNotes.width
                    spacing: 8

                    Rectangle {
                        width: notesColumn.width
                        height: notesText.implicitHeight + 40
                        color: "#191d2c"
                        clip: true

                        Text {
                            id: notesText
                            anchors.left: parent.left
                            anchors.leftMargin: 15
                            anchors.right: parent.right
                            anchors.rightMargin: 15
                            anchors.top: parent.top
                            anchors.topMargin: 20
                            color: "white"
                            font.family: "Tahoma"

                            text: "The patient presents with a history of intermittent palpitations, exertional fatigue, and occasional episodes of lightheadedness over the past several weeks. On physical examination, cardiac auscultation revealed a regular rhythm with a soft systolic murmur best heard at the left sternal border. No peripheral edema or jugular venous distention was observed. Resting blood pressure remained within normal limits, though mild tachycardia was noted during minimal exertion.
                                Electrocardiographic evaluation demonstrated sinus rhythm with sporadic premature atrial contractions and borderline QT interval prolongation. A 24‑hour Holter monitor recorded several short episodes of supraventricular tachycardia, none exceeding 12 seconds in duration. Echocardiography showed preserved left ventricular ejection fraction (approximately 58%) with no structural abnormalities, valvular defects, or pericardial effusion.
                                Laboratory findings indicated slightly elevated C‑reactive protein levels, suggesting a low‑grade inflammatory process, though cardiac biomarkers (including troponin I and BNP) remained within normal reference ranges. Thyroid function tests were unremarkable, ruling out endocrine‑related arrhythmogenic triggers.
                                Based on the current clinical picture, the symptoms are most consistent with paroxysmal supraventricular arrhythmia likely exacerbated by autonomic imbalance, stress factors, and insufficient recovery periods. At this stage, no evidence suggests acute ischemic pathology or progressive structural heart disease. The patient is advised to undergo continued monitoring, reduce stimulant intake, and maintain regular follow‑up evaluations to assess symptom progression and response to conservative management strategies."

                            font.pixelSize: 18
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }

        Rectangle {
            id: scrollBarNotes
            width: 4
            anchors.top: notesContent.top
            anchors.topMargin: 15
            anchors.bottom: notesContent.bottom
            anchors.bottomMargin: 15
            anchors.right: parent.right
            color: "#384163"
            radius: 4
            opacity: 0

            Behavior on opacity { NumberAnimation { duration: 200 } }

            Rectangle {
                id: handleNotes
                width: parent.width
                height: Math.max(40, parent.height * (flickNotes.height / flickNotes.contentHeight))

                y: Math.max(
                    0,
                    Math.min(
                        flickNotes.contentY / flickNotes.contentHeight * (scrollBarNotes.height - height),
                        scrollBarNotes.height - height
                    )
                )

                color: "#7c7c7c"
                radius: 4

                MouseArea {
                    anchors.fill: parent
                    drag.target: parent
                    drag.axis: Drag.YAxis
                    drag.minimumY: 0
                    drag.maximumY: scrollBarNotes.height - handleNotes.height

                    onPositionChanged: {
                        flickNotes.contentY =
                            handleNotes.y / (scrollBarNotes.height - handleNotes.height) * flickNotes.contentHeight
                    }
                }
            }
        }

        Timer {
            id: hideTimerNotes
            interval: 800
            repeat: false
            onTriggered: scrollBarNotes.opacity = 0
        }
    }
}