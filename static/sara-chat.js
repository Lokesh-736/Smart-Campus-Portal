(function () {
    const DEFAULT_SUGGESTIONS = [
        { title: "Today's timetable", payload: "Show timetable for today" },
        { title: "Next class & travel", payload: "What is my next class and walking time?" },
        { title: "Latest notes", payload: "Show latest notes" },
        { title: "Prep checklist", payload: "How should I prepare for class?" },
    ];

    const launcher = document.getElementById("saraLauncher");
    const panel = document.getElementById("saraPanel");
    const minimizeBtn = document.getElementById("saraMinimize");
    const closeBtn = document.getElementById("saraClose");
    const messagesEl = document.getElementById("saraMessages");
    const welcomeEl = document.getElementById("saraWelcome");
    const input = document.getElementById("saraInput");
    const sendBtn = document.getElementById("saraSend");
    const suggestionsEl = document.getElementById("saraSuggestions");

    if (!panel || !messagesEl || !input) return;

    let isOpen = false;
    let isMinimized = false;
    let isSending = false;

    function setOpen(open) {
        isOpen = open;
        panel.classList.toggle("is-open", open);
        launcher?.classList.toggle("is-open", open);
        if (open) {
            isMinimized = false;
            panel.classList.remove("is-minimized");
            setTimeout(() => input.focus(), 200);
        }
    }

    function setMinimized(minimized) {
        isMinimized = minimized;
        panel.classList.toggle("is-minimized", minimized);
    }

    function hideWelcome() {
        if (welcomeEl) welcomeEl.hidden = true;
    }

    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function autoResizeInput() {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 112) + "px";
        sendBtn.disabled = !input.value.trim() || isSending;
    }

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function appendMessage(text, role) {
        hideWelcome();
        const row = document.createElement("div");
        row.className = "sara-msg-row " + (role === "user" ? "user" : "assistant");

        if (role !== "user") {
            const avatar = document.createElement("div");
            avatar.className = "sara-msg-avatar";
            avatar.textContent = "✦";
            row.appendChild(avatar);
        }

        const bubble = document.createElement("div");
        bubble.className = "sara-bubble";
        bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
        row.appendChild(bubble);
        messagesEl.appendChild(row);
        scrollToBottom();
    }

    function appendChips(buttons) {
        if (!Array.isArray(buttons) || !buttons.length) return;
        const wrap = document.createElement("div");
        wrap.className = "sara-chips";
        buttons.slice(0, 6).forEach((btn) => {
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "sara-chip";
            chip.textContent = btn.title || "Suggestion";
            chip.addEventListener("click", () => {
                input.value = (btn.payload || "").replace(/^\/+/, "");
                autoResizeInput();
                sendMessage();
            });
            wrap.appendChild(chip);
        });
        messagesEl.appendChild(wrap);
        scrollToBottom();
    }

    function showTyping() {
        hideWelcome();
        const row = document.createElement("div");
        row.className = "sara-typing";
        row.id = "saraTypingIndicator";
        row.innerHTML =
            '<div class="sara-msg-avatar">✦</div>' +
            '<div class="sara-typing-dots"><span></span><span></span><span></span></div>';
        messagesEl.appendChild(row);
        scrollToBottom();
    }

    function hideTyping() {
        document.getElementById("saraTypingIndicator")?.remove();
    }

    async function sendMessage() {
        const text = input.value.trim();
        if (!text || isSending) return;

        isSending = true;
        sendBtn.disabled = true;
        appendMessage(text, "user");
        input.value = "";
        autoResizeInput();
        showTyping();

        try {
            const response = await fetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message: text,
                    page_path: window.location.pathname,
                    page_title: document.title,
                }),
            });
            const data = await response.json();
            hideTyping();
            appendMessage(data.reply || "I couldn't process that right now. Please try again.", "assistant");
            appendChips(data.buttons || DEFAULT_SUGGESTIONS);
        } catch {
            hideTyping();
            appendMessage("Network issue. Please check your connection and try again.", "assistant");
        } finally {
            isSending = false;
            autoResizeInput();
        }
    }

    function bindSuggestion(el, payload) {
        el.addEventListener("click", () => {
            input.value = payload;
            autoResizeInput();
            sendMessage();
        });
    }

    DEFAULT_SUGGESTIONS.forEach((item, i) => {
        const el = suggestionsEl?.children[i];
        if (el) {
            el.textContent = item.title;
            bindSuggestion(el, item.payload);
        }
    });

    launcher?.addEventListener("click", () => setOpen(true));
    closeBtn?.addEventListener("click", () => setOpen(false));
    minimizeBtn?.addEventListener("click", () => setMinimized(!isMinimized));
    sendBtn?.addEventListener("click", sendMessage);

    input.addEventListener("input", autoResizeInput);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && isOpen) setOpen(false);
    });

    autoResizeInput();
})();
