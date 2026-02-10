import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Item {
    id: mainPage
    property var deviceModel
    property var devicePage
    property string loginName: ""

    anchors.fill: parent

    //функция сортировки по приоритету
    function sortByPriority() {
        let items = [];

        for (let i = 0; i < deviceModel.count; i++) {
            items.push(JSON.parse(JSON.stringify(deviceModel.get(i))));
        }

        items.sort((a, b) => {
            if (a.alarm === "On" && b.alarm === "Off") return -1;
            if (a.alarm === "Off" && b.alarm === "On") return 1;
            return b.priority - a.priority;
        });

        deviceModel.clear();
        for (let item of items) {
            deviceModel.append(item);
        }
    }

    function mockAddItem() {
        let randomPriority = Math.floor(Math.random() * 5) + 1;
        let randomValue = Math.floor(Math.random() * 100);

        deviceModel.append({
            devicename: "MockDevice " + deviceModel.count,
            patientname: "Mock Patient",
            room: "Mock Room",
            value: randomValue.toString(),
            alarm: randomValue > 80 ? "On" : "Off",
            priority: randomPriority
        });

        sortByPriority();
    }

    function setNem(name) {
        loginName = name
    }

    // ---------------- TOP BAR ----------------
    Rectangle {
        width: parent.width
        height: parent.height * 0.15
        anchors.top: parent
        color: "#515c80"

        MouseArea {
            anchors.fill: parent
            onClicked: mockAddItem()
        }

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
            source: "qrc:/img/login.jpg"
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
                    //Описание элемента таблицы delegate
                    delegate: Rectangle {
                        id: delegateButton

                        width: column.width
                        height: column.width * 0.1
                        radius: 20

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

                        Text {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: parent.width * (0.03 + 0.026)
                            font.pixelSize: 35
                            font.family: "Tahoma"
                            text: "Room: " + room + "      " +
                                  devicename + ":" + value + "      " +
                                  patientname
                            color: "white"
                        }

                        Text {
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.rightMargin: parent.width * (0.03 + 0.026)
                            font.pixelSize: 35
                            font.family: "Tahoma"
                            text: "Priority: " + priority
                            color: "white"
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                devicePage.setDevice({
                                    devicename: devicename,
                                    patientname: patientname,
                                    room: room,
                                    value: value,
                                    alarm: alarm,
                                    priority: priority,
                                    timeout: timeout
                                })

                                mainPage.visible = false
                                devicePage.visible = true
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

