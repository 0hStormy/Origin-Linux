if ! ping -c 1 "google.com"; then
    swaybg -c '#444444' >/dev/null 2>&1 &
    env GTK_THEME=origin zenity \
    --error \
    --text="Origin Linux could not connect to the internet automatically, please set up a network connection by clicking Continue." \
    --title="Network Error" \
    --ok-label="Continue"
    st nmtui
    st sh
else
    flask --app linker run &
    chromium-browser --new-window --app=https://origin.mistium.com
fi

exit
