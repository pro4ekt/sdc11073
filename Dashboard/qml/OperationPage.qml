import QtQuick
import QtQuick.Controls
import QtQuick.Effects
/*import QtCharts
import Qt5Compat.GraphicalEffects*/

Item {
    id: opPage
    property var devicePage
    property var mainPage
    property var opname
    property var timeout
    property var alarm
    property var currentDevice: null // ADDED: reference to current Python device

    // Keep existing setOp for header/legacy if needed
    function setOp(data) {
        opname = data.opname
        timeout = data.timeout
        alarm = data.alarm
    }

    // ADDED: Function to link this page to a specific device and fetch operations
    function setDevice(device) {
        currentDevice = device
        updateOperations()
    }

    // ADDED: Listen for updates from Python
    Connections {
        target: currentDevice ? currentDevice : null
        function onOperationsChanged() { updateOperations() }
        ignoreUnknownSignals: true
    }

    // ADDED: Logic to populate ListModel from Python list
    function updateOperations() {
        opListModel.clear()
        if (currentDevice && currentDevice.operations) {
            var ops = currentDevice.operations
            for (var i = 0; i < ops.length; i++) {
                opListModel.append({
                    "name": ops[i].name,
                    "mode": ops[i].mode,
                    "handle": ops[i].handle
                })
            }
        }
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
            text: opname
            color: "white"
            font.pixelSize: 32
            font.family: "Tahoma"
        }

        Image {
            id: imageInstance
            width: parent.width * 0.1
            height: parent.height

            source: (alarm === "On" && timeout === 1) || alarm === "Ack"
                    ? "../img/noSound.png"
                    : ((alarm === "On" && timeout === 0)
                        ? "../img/bellOn.png"
                        : "../img/bellOff.png")

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
            source: "../img/homepage.png"

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    opPage.visible = false
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
            source: "../img/arrow.png"

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    opPage.visible = false
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

    // ---------------- OPERATIONS BLOCK ----------------
    Rectangle {
        id: remoteBlock
        width: parent.width * 0.3
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.topMargin: parent.height * 0.15
        color: "#191d2c"
        clip: true

        Rectangle {
            width: parent.width
            height: 50
            anchors.top: parent.top
            color: "#191d2c"

            Text {
                text: "Operations List"
                anchors.centerIn: parent
                font.pixelSize: 20
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
                // Cleared static data to support dynamic loading
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
                            // Gray out if disabled
                            color: (model.mode && model.mode !== "Enabled") ? "#2c3144" : "transparent"
                            gradient: (model.mode && model.mode !== "Enabled") ? null : gradOff

                            Gradient {
                                id: gradOff
                                GradientStop { position: 0.0; color: "#5e6ea5" }
                                GradientStop { position: 1.0; color: "#7487c4" }
                            }

                            Text {
                                id: textItem2
                                anchors.centerIn: parent
                                text: name
                                font.pixelSize: 20
                                color: (model.mode && model.mode !== "Enabled") ? "gray" : "white"
                                font.family: "Tahoma"
                            }

                            // Optional: Show mode if disabled
                            Text {
                                anchors.bottom: parent.bottom
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.bottomMargin: 5
                                text: (model.mode && model.mode !== "Enabled") ? "(" + model.mode + ")" : ""
                                font.pixelSize: 12
                                color: "#aaaaaa"
                                visible: (model.mode && model.mode !== "Enabled")
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    // Set the logic to show details for this chosen operation
                                    // You can use model.handle later to invoke it
                                    descriptionBlockContent.visible = true

                                    // Example: Update the detail view text (requires ID on text element in 3rd block)
                                    // For now just showing the block as requested
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
            anchors.bottom: remoteContent.bottom
            anchors.topMargin: 15
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

    // ---------------- OPERATION HISTORY BLOCK ----------------
    Rectangle {
        id: historyBlock
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
                text: "Operation History"
                anchors.centerIn: parent
                font.pixelSize: 32
                color: "white"
            }
        }

        Rectangle {
            id: historyBlockContent
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
                id: historyListModel
                // Cleared mock data
            }

            Flickable {
                id: flickOpsHistory
                anchors.fill: parent
                contentWidth: width
                contentHeight: opsHistoryColumn.height
                clip: true

                onMovementStarted: {
                    scrollBarOpsHistory.opacity = 1
                    hideTimerOpsHistory.restart()
                }
                onMovementEnded: hideTimerOpsHistory.restart()

                Column {
                    id: opsHistoryColumn
                    width: flickOpsHistory.width
                    spacing: 16

                    Repeater {
                        model: historyListModel

                        delegate: Rectangle {
                            width: opsColumn.width
                            height: Math.max(100, textItemHistory.implicitHeight + 20)
                            color: "#191d2c"

                            Text {
                                id: textItemHistory
                                anchors.centerIn: parent
                                text: "Name: " + name + "\n" +
                                      "Time: " + time + "\n"
                                      + " - - - - - - - - - - - - - - -"
                                font.pixelSize: 20
                                color: "white"
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: scrollBarOpsHistory
            width: 4
            anchors.top: historyBlockContent.top
            anchors.bottom: historyBlockContent.bottom
            anchors.topMargin: 15
            anchors.bottomMargin: 15
            anchors.right: parent.right
            color: "#384163"
            radius: 4
            opacity: 0

            Behavior on opacity { NumberAnimation { duration: 200 } }

            Rectangle {
                id: handleOpsHistory
                width: parent.width
                height: Math.max(40, parent.height * (flickOpsHistory.height / flickOpsHistory.contentHeight))

                y: Math.max(0, Math.min(
                        flickOpsHistory.contentY / flickOpsHistory.contentHeight * (scrollBarOpsHistory.height - height),
                        scrollBarOpsHistory.height - height
                    ))

                color: "#7c7c7c"
                radius: 4

                MouseArea {
                    anchors.fill: parent
                    drag.target: parent
                    drag.axis: Drag.YAxis
                    drag.minimumY: 0
                    drag.maximumY: scrollBarOpsHistory.height - handleOpsHistory.height

                    onPositionChanged: {
                        flickOpsHistory.contentY =
                            handleOpsHistory.y / (scrollBarOpsHistory.height - handleOpsHistory.height) * flickOpsHistory.contentHeight
                    }
                }
            }
        }

        Timer {
            id: hideTimerOpsHistory
            interval: 800
            repeat: false
            onTriggered: scrollBarOpsHistory.opacity = 0
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

        Rectangle {
            width: parent.width
            height: 50
            color: "#191d2c"

            Text {
                text: "Preform Chosen Operation"
                anchors.centerIn: parent
                font.pixelSize: 32
                color: "white"
                font.family: "Tahoma"
            }
        }

        Rectangle {
            id: descriptionBlockContent
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 50
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: parent.width * 0.02
            anchors.rightMargin: parent.width * 0.02
            clip: true
            color: "#191d2c"
            visible: false

            // ---------------- POPUP ----------------
            Popup {
                id: pop1
                modal: false
                focus: false

                x: (parent.width - width) / 2
                y: (parent.height - height) / 2

                width: parent.width * 0.8
                height: 60

                background: Rectangle {
                    radius: 10
                    color: "#2A2E32"
                    border.color: "#3A3F44"
                    border.width: 2
                }

                Text {
                    id: popupText
                    anchors.centerIn: parent
                    color: "white"
                    font.pixelSize: 18
                }

                Timer {
                    id: closeTimer
                    interval: 3000
                    repeat: false
                    onTriggered: pop1.close()
                }

                function show(msg) {
                    popupText.text = msg
                    pop1.open()
                    closeTimer.start()
                }
            }
            // ----------------------------------------

            Column {
                id: infoColumn
                spacing: 16
                anchors.fill: parent
                anchors.margins: 20

                ComboBox {
                    id: nameSelector
                    width: 400
                    height: 60
                    model: ["Hippocrates", "Dr.Dre", "Dr.Strange"]
                    x: (parent.width - width) / 2

                    contentItem: Text {
                            text: nameSelector.currentText
                            font.pixelSize: 26
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: Text.AlignHCenter
                        }

                }

                /*
                TextField {
                    id: inputField
                    width: parent.width
                    height: 40
                    placeholderText: "Enter new value..."
                    font.pixelSize: 18
                    padding: 8
                    color: "#5e6ea5"
                Text {
                    text: "Current Value: "
                    font.pixelSize: 20
                    color: "white"
                    font.family: "Tahoma"
                }

                Text {
                    text: "Last Time Changed: "
                    font.pixelSize: 20
                    color: "white"
                    font.family: "Tahoma"
                }

                }
                */

                Row {
                    spacing: 20
                    anchors.horizontalCenter: parent.horizontalCenter

                    Rectangle {
                        width: 100
                        height: 40
                        radius: 6
                        color: "#5e6ea5"

                        Text {
                            anchors.centerIn: parent
                            text: "Apply"
                            color: "white"
                            font.pixelSize: 16
                            font.bold: true
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                pop1.show("Responsible Doctor is on a way")
                            }
                        }
                    }

                    Rectangle {
                        width: 100
                        height: 40
                        radius: 6
                        color: "#af3c3c"

                        Text {
                            anchors.centerIn: parent
                            text: "Cancel"
                            color: "white"
                            font.pixelSize: 16
                            font.bold: true
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                pop1.show("Canceled")
                            }
                        }
                    }
                }
            }
        }
    }
}