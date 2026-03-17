import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Item {
    id: mainPage
    // Changed from 'var' to ListModel so we can append to it directly here
    property ListModel deviceModel: ListModel {}
    property var devicePage
    property string loginName: ""

    anchors.fill: parent

    // LINK TO PYTHON BACKEND
    Connections {
        target: sdcManager // This is the context property set in main.py

        // Signal handler for deviceConnected(QtDeviceHandler device)
        function onDeviceConnected(device) {
            console.log("QML: New device connected: " + device.patientName + " [" + device.epr + "]")

            // Append data from the Python object to our QML model
            deviceModel.append({
                "epr": device.epr, // Store UUID to identify this item later
                "devicename": device.deviceName, // CHANGED: Bind to actual property
                "patientname": device.patientName,
                "room": device.patientRoom,
                "value": device.deviceValue,
                "alarm": device.alarmStatus,
                "priority": device.priority,
                "metrics": device.metrics, // Initial metrics
                "deviceObj": device, // Store the Python Object for live updates via Connections
                "timeout": "0"
            })
            // Trigger sort when new device appears
            sortTimer.restart()
        }

        // Signal handler for deviceDisconnected(str epr)
        function onDeviceDisconnected(epr) {
            console.log("QML: Device disconnected: " + epr)
            for (var i = 0; i < deviceModel.count; ++i) {
                if (deviceModel.get(i).epr === epr) {
                    deviceModel.remove(i)
                    break // Stop after removing found item
                }
            }
        }
    }

    // Timer to debounce sorting so UI doesn't stutter on multiple rapid updates
    Timer {
        id: sortTimer
        interval: 100 // Wait 100ms after last update then sort
        repeat: false
        onTriggered: sortByPriority()
    }

    //функция сортировки по приоритету
    function sortByPriority() {
        let items = [];

        // Manual copy to preserve deviceObj reference (JSON.stringify destroys Python objects)
        for (let i = 0; i < deviceModel.count; i++) {
            let item = deviceModel.get(i);
            items.push({
                "epr": item.epr,
                "devicename": item.devicename,
                "patientname": item.patientname,
                "room": item.room,
                "value": item.value,
                "alarm": item.alarm,
                "priority": item.priority,
                "metrics": item.metrics,
                "deviceObj": item.deviceObj, // Keep the connection target alive!
                "timeout": item.timeout
            });
        }

        items.sort((a, b) => {
            // Sort Logic: Alarm 'On' > Alarm 'Off' > Priority High to Low
            let alarmA = (a.alarm === "On");
            let alarmB = (b.alarm === "On");

            if (alarmA && !alarmB) return -1;
            if (!alarmA && alarmB) return 1;

            return parseInt(b.priority) - parseInt(a.priority);
        });

        // Re-populate the models
        deviceModel.clear();
        for (let item of items) {
            deviceModel.append(item);
        }
    }

    // ---------------- TOP BAR ----------------
    Rectangle {
        width: parent.width
        height: parent.height * 0.15
        anchors.top: parent
        color: "#515c80"

        // текст в TOP BAR
        Text {
            anchors.left: parent.left
            anchors.leftMargin: 25
            anchors.verticalCenter: parent.verticalCenter
            text: loginName + "Patients(Devices)"
            color: "white"
            font.pixelSize: 32
            font.family: "Tahoma"
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

        /*
        //  картинка Login
        Image {
            width: 40
            height: 40
            anchors.right: parent.right
            anchors.rightMargin: parent.width * 0.02
            anchors.verticalCenter: parent.verticalCenter
            source: "img/login.jpg"
        }
        */
    }

    // ---------------- CONTENT AREA ----------------
    Rectangle {
        id: contentArea
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: parent.height * 0.15
        anchors.left: parent.left
        anchors.right: parent.right
        color: "#191d2c"
        clip: true
        //flickable block с ScrollBar
        Flickable {
            id: flick
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: parent.height * 0.05
            anchors.bottomMargin: parent.height*0.05
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: parent.width * 0.02
            anchors.rightMargin: parent.width * 0.02
            contentWidth: width
            contentHeight: column.height
            clip: true

            onMovementStarted: {
                scrollBar.opacity = 1
                hideTimer.restart()
            }

            onMovementEnded: hideTimer.restart()
            //Column с кнопками
            Column {
                id: column
                width: flick.width
                spacing: 16
                //Repeater где добавляются все элементы
                Repeater {
                    model: deviceModel
                    delegate: Rectangle {
                        id: delegateButton

                        // LIVE UPDATES: Listen to the specific python object for this row
                        Connections {
                            target: model.deviceObj
                            function onDeviceValueChanged() { model.value = target.deviceValue }
                            function onPatientNameChanged() { model.patientname = target.patientName }
                            function onPatientRoomChanged() { model.room = target.patientRoom }
                            function onDeviceNameChanged() { model.devicename = target.deviceName }
                            function onMetricsChanged() { model.metrics = target.metrics }
                            // ADDED: Listen for live Alarm and Priority updates AND trigger Sort
                            function onAlarmStatusChanged() {
                                model.alarm = target.alarmStatus;
                                sortTimer.restart();
                            }
                            function onPriorityChanged() {
                                model.priority = target.priority;
                                sortTimer.restart();
                            }
                        }

                        width: column.width
                        height: 80 // Reverted to a smaller/standard size or use: width * 0.15 if you had relative
                        radius: 20

                        // Fix: use model.alarm from the ListModel
                        color: model.alarm === "On" ? "transparent" : "#5e6ea5"

                        // Выбираем градиент в зависимости от Alarm
                        gradient: model.alarm === "On" ? gradOn : gradOff

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
                            running: model.alarm === "On"
                            loops: Animation.Infinite

                            NumberAnimation { to: 0.5; duration: 500; easing.type: Easing.InOutQuad }
                            NumberAnimation { to: 1.0; duration: 500; easing.type: Easing.InOutQuad }

                            // FIXED: Reset opacity when alarm stops to prevent "stuck" semi-transparent colors
                            onRunningChanged: {
                                if (!running) {
                                    delegateButton.opacity = 1.0
                                }
                            }
                        }

                        Text {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 20
                            font.pixelSize: 24
                            font.family: "Tahoma"
                            // Use 'model.' prefix to be explicit and safe
                            // ADDED: Showing main value (Alarm metric or Last metric)
                            text: "Room: " + (model.room ? model.room : "?") + " | " +
                                  (model.devicename ? model.devicename : "Unknown") + " | " +
                                  (model.value ? model.value : "---") + " | " +
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
                                // Reverted to your original simple check, just ensuring mainPage exists
                                if (mainPage.devicePage) {
                                    // PASS THE RAW PYTHON OBJECT DIRECTLY
                                    // This ensures we get the live 'metrics' list directly from main.py
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

    // ---------------- GLOBAL SCROLLBAR (FIXED RIGHT) ----------------
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

        Behavior on opacity {
            NumberAnimation { duration: 200 }
        }

        Rectangle {
            id: handle
            width: parent.width
            height: Math.max(40, parent.height * (flick.height / flick.contentHeight))

            y: Math.max(0, Math.min(
                    flick.contentY / flick.contentHeight * (scrollBar.height - height),
                    scrollBar.height - height
                ))

            color: "#7c7c7c"
            radius: 4

            MouseArea {
                anchors.fill: parent
                drag.target: parent
                drag.axis: Drag.YAxis
                drag.minimumY: 0
                drag.maximumY: scrollBar.height - handle.height

                onPositionChanged: {
                    flick.contentY =
                        handle.y / (scrollBar.height - handle.height) * flick.contentHeight
                }
            }
        }
    }
    // ---------------- SCROLLBAR AUTO-HIDE TIMER ----------------
    Timer {
        id: hideTimer
        interval: 800
        repeat: false
        onTriggered: scrollBar.opacity = 0
    }
}