import QtQuick
import QtQuick.Controls
import QtQuick.Effects

Item {
    id: loginPage
    property var mainPage
    property string loginName: loginField.text

    anchors.fill: parent

    Column {
        id: loginForm
        spacing: 20
        width: parent.width * 0.4
        anchors.topMargin: parent.height * 0.25
        anchors.centerIn: parent

        TextField {
            id: loginField
            width: parent.width
            height: 40

            padding: 0
            topPadding: 8
            bottomPadding: 8

            placeholderText: "Login"
            font.pixelSize: 16
            color: "black"
            placeholderTextColor: "gray"

            background: Rectangle {
                radius: 6
                border.color: "black"
                color: "white"
            }
        }

        TextField {
            id: passwordField
            width: parent.width
            height: 40

            padding: 0
            topPadding: 8
            bottomPadding: 8

            placeholderText: "Password"
            echoMode: TextInput.Password
            font.pixelSize: 16
            color: "black"
            placeholderTextColor: "gray"

            background: Rectangle {
                radius: 6
                border.color: "black"
                color: "white"
            }
        }

        Button {
            id: loginButton
            width: parent.width
            height: 40
            text: "Login"
            font.pixelSize: 16

            onClicked: {
                //mainPage.setName(loginName)
                loginPage.visible = false
                mainPage.visible = true
            }
        }
    }
}
