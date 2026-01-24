(this dir is a temporary dir, where MacOS will put screenshots made with cmd-shift-3 etc.

To configure MacOS to put its screenshots here, execute these 2 lines in Terminal:

```
defaults write com.apple.screencapture location ~/Utilities/macos-screenshot-renamer/raw-screenshots/
killall SystemUIServer
```

If the screenshot-renamer is working properly, this directory will be empty, because the renamer will move screenshots from raw-screenshots/ back to ~/Desktop/ .

)