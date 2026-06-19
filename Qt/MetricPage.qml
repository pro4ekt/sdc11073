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
    property var graphPoints: [] // Массив для хранения истории значений графика

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

                var incomingValue = list[i].value;
                var incomingSamples = list[i].samples;

                // Handle Waveform Logic
                if (incomingValue === "Waveform") {
                    value = "Report Recieved"; // Display Text

                    // Graphing Logic for Waveforms (Arrays)
                    if (incomingSamples && incomingSamples.length > 0) {
                        var temp = graphPoints;
                        for (var j = 0; j < incomingSamples.length; j++) {
                            temp.push(incomingSamples[j]);
                        }
                        // Increase buffer size for waveforms (fast data)
                        // CHANGED: Increased from 300 to 1000 for 5 seconds of history @ 200Hz
                        while (temp.length > 1000) {
                            temp.shift();
                        }
                        graphPoints = temp;
                        graph.requestPaint();
                    }

                } else {
                    // Standard Numeric Logic
                    value = incomingValue;

                    // --- ЛОГИКА ГРАФИКА ---
                    var fVal = parseFloat(value);
                    if (!isNaN(fVal)) {
                        // Манипуляция с массивом
                        var temp2 = graphPoints
                        temp2.push(fVal)
                        // Храним последние 50 точек
                        if (temp2.length > 50) temp2.shift()
                        graphPoints = temp2
                        // Перерисовать график
                        graph.requestPaint()
                    }
                    // ---------------------
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
        var newMetricname = data.metricname || "Unknown"
        var isNewMetric = (metricname !== newMetricname)

        metricname = newMetricname

        // Handle initial value display exception for Waveform
        if (data.value === "Waveform") {
            value = "Report Recieved"
        } else {
            value = data.value || "--"
        }

        alarm = data.alarm || "Off"
        // Reset timeout only when opening a DIFFERENT metric —
        // preserves the local bell-silence state when re-entering the same metric page.
        if (isNewMetric) {
            timeout = 0
        }

        // Очищаем историю графика при входе в новую метрику
        graphPoints = []
        graph.requestPaint()

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

        // ======== ГРАФИК ========
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
                    var w = width
                    var h = height

                    ctx.clearRect(0, 0, w, h)

                    // ФОН ГРАФИКА
                    ctx.fillStyle = "#191d2c"
                    ctx.fillRect(0, 0, w, h)

                    // Отступы
                    var left = 50
                    var right = 20
                    var top = 20
                    var bottom = 30

                    var graphW = w - left - right
                    var graphH = h - top - bottom

                    // --- ОСИ И СЕТКА ---
                    // Рамка осей
                    ctx.lineWidth = 2
                    ctx.strokeStyle = "#8fa3ff"
                    ctx.beginPath()
                    ctx.moveTo(left, top)
                    ctx.lineTo(left, h - bottom)
                    ctx.lineTo(w - right, h - bottom)
                    ctx.stroke()

                    // Если нет точек, на этом всё
                    if (graphPoints.length < 1) return

                    // Определяем min/max для масштабирования Y
                    var minVal = graphPoints[0]
                    var maxVal = graphPoints[0]
                    for (var i = 1; i < graphPoints.length; i++) {
                        if (graphPoints[i] < minVal) minVal = graphPoints[i]
                        if (graphPoints[i] > maxVal) maxVal = graphPoints[i]
                    }

                    // Добавляем отступы (padding) по вертикали, чтобы график не «прилипал»
                    var range = maxVal - minVal
                    if (range === 0) range = 10 // Защита от деления на 0
                    var yMin = minVal - range * 0.1
                    var yMax = maxVal + range * 0.1
                    var yRange = yMax - yMin

                    // --- СЕТКА (Grid) ---
                    ctx.strokeStyle = "#2a3045"
                    ctx.lineWidth = 1
                    ctx.fillStyle = "#c7d0ff"
                    ctx.font = "12px sans-serif"

                    // Рисуем 5 горизонтальных линий
                    for (var j = 0; j <= 4; j++) {
                         var val = yMin + (j / 4.0) * yRange
                         var yPos = h - bottom - (j / 4.0) * graphH

                         ctx.beginPath()
                         ctx.moveTo(left, yPos)
                         ctx.lineTo(w - right, yPos)
                         ctx.stroke()

                         // Подпись значений оси Y
                         ctx.fillText(val.toFixed(1), 5, yPos + 4)
                    }

                    // --- ЛИНИЯ ГРАФИКА ---
                    if (graphPoints.length > 1) {
                        ctx.strokeStyle = "#ff4d4d"
                        ctx.lineWidth = 3
                        ctx.beginPath()

                        // Растягиваем массив точек по ширине graphW
                        var stepX = graphW / (graphPoints.length - 1)

                        for (var k = 0; k < graphPoints.length; k++) {
                            var x = left + k * stepX

                            // Нормализуем значение Y от 0 до 1
                            var normalizedY = (graphPoints[k] - yMin) / yRange
                            // Переводим в координаты canvas (снизу вверх)
                            var y = h - bottom - normalizedY * graphH

                            if (k === 0) ctx.moveTo(x, y)
                            else ctx.lineTo(x, y)
                        }
                        ctx.stroke()
                    }
                }
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