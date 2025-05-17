currentgtk=$GTK_THEME
devmode=true

export GTK_THEME=theme

sleep 2

if ! ping -c 1 "google.com"; then
    swaybg -c '#444444' >/dev/null 2>&1 &
    zenity \
    --error \
    --text="Origin Linux could not connect to the internet automatically, please set up a network connection by clicking Continue." \
    --title="Network Error" \
    --ok-label="Continue"
    foot nm-connection-editor
    foot sh
else
    if [ "$devmode" = true ]; then
        foot sh &
    fi
    flask --app linker run &
    chromium --new-window --app=https://origin.mistium.com
fi

export GTK_THEME=$currentgtk
exit
