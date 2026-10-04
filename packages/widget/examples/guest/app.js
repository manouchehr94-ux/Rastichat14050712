const status = document.getElementById("status");

const apiBase = window.location.origin + "/api/v1";
const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const wsBase = wsProtocol + "//" + window.location.host + "/ws";

fetch(apiBase + "/health/live/")
    .then((response) => {
        if (!response.ok) throw new Error("Backend unavailable");
        return response.json();
    })
    .then(() => {
        status.innerHTML = "✅ پشتیبانی آماده است";
    })
    .catch(() => {
        status.innerHTML = "❌ ارتباط با سرور برقرار نشد";
    });

window.RastiChat.init({
    projectKey: "YOUR_PUBLIC_PROJECT_UUID",
    apiBase: apiBase,
    wsBase: wsBase,
    position: "right",
    primaryColor: "#BC5A38"
});
