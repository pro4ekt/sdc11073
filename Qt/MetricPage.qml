import QtQuick
import QtQuick.Controls
import QtQuick.Effects
//import Qt5Compat.GraphicalEffects

Item {
    id: metricPage
    property var devicePage
    property var mainPage
    property string metricname: ""
    property string value: ""
    property string alarm: ""
    property int timeout: 0

    // NEW: Listen for dynamic updates from the specific device object
    Connections {
        target: (devicePage && devicePage.currentDevice) ? devicePage.currentDevice : null
        function onMetricsChanged() {
            updateDynamicValues()
        }
    }

    function updateDynamicValues() {
        if (!devicePage || !devicePage.currentDevice) return;

        var list = devicePage.currentDevice.metrics;
        // Iterate through fresh metrics to find ours by its Handle (metricname)
        for (var i = 0; i < list.length; i++) {
            if (list[i].metricname === metricname) {
                // Exclude updating value for Waveforms as requested
                if (list[i].value !== "Waveform") {
                    value = list[i].value;
                }
                alarm = list[i].alarm;

                // Add report to history (dynamic logging)
                var reportVal = list[i].value;
                if (reportVal === "Waveform") {
                    reportVal = "Report Recieved";
                }
                addReportItem(reportVal, list[i].alarm);
                break;
            }
        }
    }

    function addReportItem(val, alm) {
        var now = new Date();
        // Format: dd.MM.yy HH:mm:ss
        var timeStr = now.toLocaleString(Qt.locale(), "dd.MM.yy HH:mm:ss");

        opListModel.append({
            "value": val,
            "time": timeStr,
            "alarm": alm
        });

        // If more than 40 reports, clear and start over as requested
        if (opListModel.count > 40) {
            opListModel.clear();
        }
    }

    function setMetric(data) {
        metricname = data.metricname || "Unknown"
        value = data.value || "--"
        alarm = data.alarm || "Off"
        // Ensure timeout is handled if present, else 0
        timeout = data.timeout ? data.timeout : 0

        // Clear reports when entering a new metric page
        if (opListModel) opListModel.clear();
    }

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
            text: metricname
            font.pixelSize: 32
            color: "white"
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

        Image {
            id: imageInstance
            width: parent.width * 0.1
            height: parent.height

            source: alarm === "On" && timeout === 1
                    ? "img/noSound.png"
                    : (alarm === "On" && timeout === 0
                        ? "img/bellOn.png"
                        : "img/bellOff.png")

            fillMode: Image.PreserveAspectCrop
            layer.enabled: true
            /*
            layer.effect: OpacityMask {
                maskSource: Rectangle {
                    width: imageInstance.width
                    height: imageInstance.height
                    radius: imageInstance.radius
                }
            }
            */

            MouseArea {
                anchors.fill: parent
                onPressed: {
                    if (alarm === "On") {
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
                    metricPage.visible = false
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
                    metricPage.visible = false
                    devicePage.visible = true
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

    // ---------------- METRIC INFO BLOCK ----------------
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
                text: "Vital Info"
                color: "white"
                anchors.centerIn: parent
                font.pixelSize: 32
                font.family: "Tahoma"
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

            Flickable {
                id: flickMetrics
                anchors.fill: parent
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
                    spacing: 8

                    Rectangle {
                        width: metricsColumn.width
                        height: textItem.implicitHeight + 40
                        color: "#191d2c"
                        clip: true

                        Text {
                            id: textItem
                            anchors.left: parent.left
                            anchors.leftMargin: 15
                            anchors.right: parent.right
                            anchors.rightMargin: 15
                            anchors.top: parent.top
                            anchors.topMargin: 20
                            color: "white"

                            text:
                                "Metric Handle: " + metricname + "\n" +
                                "\n" +
                                "Current Value: " + value + "\n" +
                                "Alarm Status: " + alarm + "\n" +
                                "\n" +
                                "Last Update: " + new Date().toLocaleTimeString()

                            font.pixelSize: 18
                            wrapMode: Text.Wrap
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
            anchors.right: parent.right
            anchors.bottomMargin: 15
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

    // ---------------- METRIC REPORTS BLOCK ----------------
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
                text: "Vital Reports"
                anchors.centerIn: parent
                font.pixelSize: 32
                color: "white"
                font.family: "Tahoma"
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
                // Mock data removed for dynamic operation
            }

            Flickable {
                id: flickOps
                anchors.fill: parent
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
                    spacing: 8

                    Repeater {
                        model: opListModel

                        delegate: Rectangle {
                            width: opsColumn.width
                            height: Math.max(100, textItem2.implicitHeight + 20)
                            color: "#191d2c"

                            Text {
                                id: textItem2
                                anchors.centerIn: parent
                                text: "Curent Value: " + value + "\n"
                                      + "Time: " + time + "\n"
                                      + "Alarm: " + alarm + "\n"
                                      + " - - - - - - - - - - - - - - -"
                                font.pixelSize: 20
                                font.family: "Tahoma"
                                color: "white"
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    opPage.setOp({
                                    opname: opname
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
            anchors.bottomMargin: 15
            anchors.bottom: remoteContent.bottom
            anchors.topMargin: 15
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
        id: thirdBlock
        width: parent.width * 0.4
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.leftMargin: parent.width * 0.6
        anchors.topMargin: parent.height * 0.15
        color: "#191d2c"
        clip: true

        // ======== ГРАФИК СВЕРХУ ========
        Rectangle {
            id: graphContainer
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: parent.height * 0.75

            Canvas {
                id: graph
                anchors.fill: parent

                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)

                    // ФОН ГРАФИКА
                    ctx.fillStyle = "#191d2c"
                    ctx.fillRect(0, 0, width, height)

                    // Отступы
                    var left = 50
                    var right = 10
                    var top = 10
                    var bottom = 30

                    var w = width - left - right
                    var h = height - top - bottom

                    // --- СЕТКА ---
                    ctx.strokeStyle = "#2a3045"
                    ctx.lineWidth = 1

                    var gridY = 4
                    for (var gy = 0; gy <= gridY; gy++) {
                        var y = top + gy * (h / gridY)
                        ctx.beginPath()
                        ctx.moveTo(left, y)
                        ctx.lineTo(width - right, y)
                        ctx.stroke()
                    }

                    var gridX = 5
                    for (var gx = 0; gx <= gridX; gx++) {
                        var x = left + gx * (w / gridX)
                        ctx.beginPath()
                        ctx.moveTo(x, top)
                        ctx.lineTo(x, height - bottom)
                        ctx.stroke()
                    }

                    // --- ОСИ ---
                    ctx.strokeStyle = "#8fa3ff"
                    ctx.lineWidth = 2

                    ctx.beginPath()
                    ctx.moveTo(left, top)
                    ctx.lineTo(left, height - bottom)
                    ctx.stroke()

                    ctx.beginPath()
                    ctx.moveTo(left, height - bottom)
                    ctx.lineTo(width - right, height - bottom)
                    ctx.stroke()

                    // --- ЛИНИЯ ГРАФИКА ---
                    ctx.strokeStyle = "#ff4d4d"
                    ctx.lineWidth = 3

                    ctx.beginPath()
                    ctx.moveTo(left, height - bottom - 40)
                    ctx.lineTo(width - right, top + 20)
                    ctx.stroke()

                    // --- ТЕКСТ ---
                    ctx.fillStyle = "#c7d0ff"
                    ctx.font = "12px sans-serif"

                    var yLabels = [60, 70, 80, 90, 100]
                    for (var j = 0; j < yLabels.length; j++) {
                        var ly = top + (100 - yLabels[j]) * (h / 40)
                        ctx.fillText(yLabels[j], 10, ly + 4)
                    }

                    var times = ["13:00", "13:20", "13:40", "14:00", "14:15"]
                    for (var t = 0; t < times.length; t++) {
                        var tx = left + t * (w / (times.length - 1))
                        ctx.fillText(times[t], tx - 15, height - 10)
                    }
                }

                Component.onCompleted: requestPaint()
            }
        }

        // ======== ПАНЕЛЬ СНИЗУ ========
        Rectangle {
            id: bottomPanel
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: parent.height * 0.25
            color: "#191d2c"

            Text {
                text: "Alarm started: 14:05" + "\n" +
                "Duration: 07:00" + "\n" +
                "Trend: Rising" + "\n" +
                "Average: 72 bpm"
                font.pixelSize: 20
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.leftMargin: 15
                anchors.topMargin: 15
                color: "white"
            }
        }
    }
}
