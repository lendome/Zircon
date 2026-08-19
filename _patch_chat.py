import sys

path = 'zirconAgent/cli/tui/components/chat.py'
data = open(path, 'r', encoding='utf-8').read()

# Step 4: Modify escape handling
old4 = (
    '        # Escape \u2014 clear input or exit shell mode\n'
    '        if key == "escape":\n'
    '            if self._shell.active:\n'
    '                self._shell.exit()\n'
    '                self._prompt.mode = PromptMode.NORMAL\n'
    '            else:\n'
    '                self._input.set_text("")\n'
    '            self._autocomplete.hide()\n'
    '            self._render()\n'
    '            return'
)
new4 = (
    '        # Escape \u2014 stop streaming, clear input, or double-escape for checkpoints\n'
    '        if key == "escape":\n'
    '            now = time.time()\n'
    '            is_double = (now - self._last_escape_time) < self._double_escape_threshold\n'
    '            self._last_escape_time = now\n'
    '\n'
    '            # If streaming, single escape stops the current turn\n'
    '            if self._is_streaming.get():\n'
    '                if is_double:\n'
    '                    self._cancel_streaming()\n'
    '                    await self._show_checkpoint_picker()\n'
    '                else:\n'
    '                    self._cancel_streaming()\n'
    '                    self._toast_mgr.info("Turn stopped. You can type now.")\n'
    '                return\n'
    '\n'
    '            # Not streaming: double-escape shows checkpoint picker\n'
    '            if is_double:\n'
    '                self._last_escape_time = 0.0\n'
    '                await self._show_checkpoint_picker()\n'
    '                return\n'
    '\n'
    '            # Single escape (not streaming): clear input or exit shell mode\n'
    '            if self._shell.active:\n'
    '                self._shell.exit()\n'
    '                self._prompt.mode = PromptMode.NORMAL\n'
    '            else:\n'
    '                self._input.set_text("")\n'
    '            self._autocomplete.hide()\n'
    '            self._render()\n'
    '            return'
)
assert old4 in data, 'old4 not found'
data = data.replace(old4, new4, 1)

# Step 5: Modify _submit to create checkpoint before streaming
old5 = (
    '        # Regular chat \u2014 print "You:" then stream directly\n'
    '        theme = self._theme_signal.get()\n'
    '        self.console.print(Text(f"  You: {text}", style=theme.success.to_rich()))\n'
    '        buf = TextBuffer()\n'
    '        await self._stream_chat(text, buf)\n'
    '        self._render_lines = 0\n'
    '        self._render()'
)
new5 = (
    '        # Regular chat \u2014 create checkpoint, print "You:" then stream directly\n'
    '        theme = self._theme_signal.get()\n'
    '        # Create a git checkpoint before the agent turn for reversibility\n'
    '        try:\n'
    '            cp = await self._checkpoint_mgr.create_checkpoint(label=text[:60])\n'
    '            if cp is not None:\n'
    '                self._toast_mgr.info(f"Checkpoint: {cp.sha}", duration=1.0)\n'
    '        except Exception:\n'
    '            pass\n'
    '        self.console.print(Text(f"  You: {text}", style=theme.success.to_rich()))\n'
    '        buf = TextBuffer()\n'
    '        await self._stream_chat(text, buf)\n'
    '        self._render_lines = 0\n'
    '        self._render()'
)
assert old5 in data, 'old5 not found'
data = data.replace(old5, new5, 1)

# Step 6: Modify /task to create checkpoint before streaming
old6 = (
    '            self.console.print(Text(f"Task: {arg}", style=theme.warning.to_rich()))\n'
    '            buf = TextBuffer()\n'
    '            await self._stream_task(arg, buf)\n'
    '            return False'
)
new6 = (
    '            self.console.print(Text(f"Task: {arg}", style=theme.warning.to_rich()))\n'
    '            # Create a git checkpoint before the task for reversibility\n'
    '            try:\n'
    '                cp = await self._checkpoint_mgr.create_checkpoint(label=f"task: {arg[:50]}")\n'
    '                if cp is not None:\n'
    '                    self._toast_mgr.info(f"Checkpoint: {cp.sha}", duration=1.0)\n'
    '            except Exception:\n'
    '                pass\n'
    '            buf = TextBuffer()\n'
    '            await self._stream_task(arg, buf)\n'
    '            return False'
)
assert old6 in data, 'old6 not found'
data = data.replace(old6, new6, 1)

# Step 7: Add _cancel_streaming, _show_checkpoint_picker, _handle_checkpoint_picker_key methods
old7 = '    async def _stream_chat(self, message: str, buf: TextBuffer) -> None:'
new7 = '''    def _cancel_streaming(self) -> None:
        """Cancel the current streaming agent turn."""
        self._streaming_cancelled = True
        if self._streaming_task is not None and not self._streaming_task.done():
            self._streaming_task.cancel()
        self._is_streaming.set(False)
        self._footer.update(is_active=False, status_message="Stopped")
        self._clear_render()

    async def _show_checkpoint_picker(self) -> bool:
        """Show the checkpoint picker for reverting to a previous state."""
        theme = self._theme_signal.get()
        try:
            checkpoints = await self._checkpoint_mgr.list_checkpoints(20)
        except Exception as exc:
            self._toast_mgr.error(f"Could not load checkpoints: {exc}")
            self._render_lines = 0
            self._render()
            return False
        if not checkpoints:
            self._toast_mgr.info("No checkpoints available")
            self._render_lines = 0
            self._render()
            return False
        self._checkpoint_picker = CheckpointPicker(checkpoints, theme)
        self._render_lines = 0
        self._render()
        return False

    async def _handle_checkpoint_picker_key(self, key: str) -> None:
        """Handle keys while the checkpoint picker is visible."""
        picker = self._checkpoint_picker
        if picker is None:
            return
        picker.handle_key(key)
        if not picker.is_visible:
            if picker.cancelled:
                self._checkpoint_picker = None
                self._render_lines = 0
                self._render()
                return
            # User confirmed a checkpoint selection
            selected = picker.selected
            self._checkpoint_picker = None
            if selected is not None:
                self._clear_render()
                theme = self._theme_signal.get()
                self.console.print(Text(
                    f"  Reverting to checkpoint {selected.sha}...",
                    style=theme.warning.to_rich(),
                ))
                try:
                    ok = await self._checkpoint_mgr.revert_checkpoint(selected.sha)
                except Exception as exc:
                    self._toast_mgr.error(f"Revert failed: {exc}")
                    ok = False
                if ok:
                    self._toast_mgr.success(f"Reverted to {selected.sha}")
                    self.console.print(Text(
                        f"  {selected.message}",
                        style=f"dim {theme.text_muted.to_rich()}",
                    ))
                else:
                    self._toast_mgr.error("Could not revert to checkpoint")
            self._render_lines = 0
            self._render()
            return
        self._render()

    async def _stream_chat(self, message: str, buf: TextBuffer) -> None:'''
assert old7 in data, 'old7 not found'
data = data.replace(old7, new7, 1)

# Step 8: Wrap _stream_chat body with streaming flag + cancellation check
old8 = '        try:\n            async for chunk in self._transport.chat_stream(message):'
new8 = '        self._is_streaming.set(True)\n        self._streaming_cancelled = False\n        try:\n            async for chunk in self._transport.chat_stream(message):\n                if self._streaming_cancelled:\n                    break'
assert old8 in data, 'old8 not found'
data = data.replace(old8, new8, 1)

# Step 9: Add streaming flag reset in _stream_chat finally block
old9 = '        finally:\n            close_reasoning()\n            close_activity_live()\n            close_tool_live()\n            flush_text()\n\n        self._footer.update(is_active=False, status_message="idle")\n\n    async def _stream_task'
new9 = '        finally:\n            close_reasoning()\n            close_activity_live()\n            close_tool_live()\n            flush_text()\n            self._is_streaming.set(False)\n            self._streaming_cancelled = False\n\n        self._footer.update(is_active=False, status_message="idle")\n\n    async def _stream_task'
assert old9 in data, 'old9 not found'
data = data.replace(old9, new9, 1)

# Step 10: Wrap _stream_task body with streaming flag + cancellation check
old10 = '        try:\n            async for event in self._transport.solve_stream(task):'
new10 = '        self._is_streaming.set(True)\n        self._streaming_cancelled = False\n        try:\n            async for event in self._transport.solve_stream(task):\n                if self._streaming_cancelled:\n                    break'
assert old10 in data, 'old10 not found'
data = data.replace(old10, new10, 1)

# Step 11: Add streaming flag reset in _stream_task finally block
old11 = '        finally:\n            close_activity_live()\n\n        self._footer.update(is_active=False, status_message="idle")'
new11 = '        finally:\n            close_activity_live()\n            self._is_streaming.set(False)\n            self._streaming_cancelled = False\n\n        self._footer.update(is_active=False, status_message="idle")'
assert old11 in data, 'old11 not found'
data = data.replace(old11, new11, 1)

open(path, 'w', encoding='utf-8').write(data)
print('OK: all steps done')