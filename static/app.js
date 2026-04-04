(() => {
    const progressPanel = document.querySelector(".progress-panel");
    if (!progressPanel) {
        return;
    }

    const progressFill = progressPanel.querySelector(".progress-fill");
    const progressValue = progressPanel.querySelector(".progress-value");
    const progressMessage = progressPanel.querySelector(".progress-message");
    const progressLink = progressPanel.querySelector(".progress-link");
    const downloadButtons = Array.from(document.querySelectorAll(".download-form button"));
    let pollingTimer = null;

    const setButtonsDisabled = (disabled) => {
        for (const button of downloadButtons) {
            button.disabled = disabled;
        }
    };

    const showProgress = (progress, message, state) => {
        const safeProgress = Math.max(0, Math.min(100, Math.round(progress || 0)));
        progressPanel.hidden = false;
        progressPanel.dataset.state = state || "downloading";
        progressFill.style.width = `${safeProgress}%`;
        progressValue.textContent = `${safeProgress}%`;
        progressMessage.textContent = message || "Preparing download...";
    };

    const stopPolling = () => {
        if (pollingTimer !== null) {
            window.clearTimeout(pollingTimer);
            pollingTimer = null;
        }
    };

    const pollJob = async (jobId) => {
        try {
            const response = await fetch(`/download-status/${jobId}`, { cache: "no-store" });
            if (!response.ok) {
                throw new Error("Could not read download progress.");
            }

            const job = await response.json();
            showProgress(job.progress, job.message, job.state);

            if (job.state === "completed") {
                stopPolling();
                setButtonsDisabled(false);
                progressLink.href = job.download_url;
                progressLink.hidden = false;
                progressMessage.textContent = "Download is ready. It should start automatically.";
                window.location.assign(job.download_url);
                return;
            }

            if (job.state === "error") {
                stopPolling();
                setButtonsDisabled(false);
                progressLink.hidden = true;
                return;
            }

            pollingTimer = window.setTimeout(() => pollJob(jobId), 900);
        } catch (error) {
            stopPolling();
            setButtonsDisabled(false);
            progressLink.hidden = true;
            showProgress(0, error.message || "Download failed.", "error");
        }
    };

    for (const form of document.querySelectorAll(".download-form")) {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            stopPolling();
            setButtonsDisabled(true);
            progressLink.hidden = true;
            showProgress(2, "Creating download job...", "starting");

            try {
                const response = await fetch("/start-download", {
                    method: "POST",
                    body: new URLSearchParams(new FormData(form)),
                });
                if (!response.ok) {
                    throw new Error("Could not start the download.");
                }

                const data = await response.json();
                pollJob(data.job_id);
            } catch (error) {
                setButtonsDisabled(false);
                showProgress(0, error.message || "Could not start the download.", "error");
            }
        });
    }
})();
