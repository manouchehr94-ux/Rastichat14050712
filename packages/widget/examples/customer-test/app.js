const status = document.getElementById("status");

const apiBase = "http://localhost:18080/api/v1";
const wsBase = "ws://localhost:18080/ws";

fetch(`${apiBase}/health/live/`)
    .then((response) => {
        if (!response.ok) {
            throw new Error("Backend unavailable");
        }
        return response.json();
    })
    .then(() => {
        status.innerHTML = "✅ Backend متصل است";
    })
    .catch(() => {
        status.innerHTML = "❌ Backend در دسترس نیست";
    });

const projectKey =
    new URLSearchParams(window.location.search).get("projectKey") ||
    window.localStorage.getItem("rastichat_demo_project_key");

if (!projectKey) {
    status.innerHTML = `
        ⚠️ Project Key مشخص نشده است.<br><br>
        آدرس صفحه را به این شکل باز کنید:<br>
        <code>?projectKey=YOUR_PROJECT_KEY</code>
    `;
} else {
    window.localStorage.setItem("rastichat_demo_project_key", projectKey);

    window.RastiChat.init({
        projectKey,
        apiBase,
        wsBase,
        position: "right",
        primaryColor: "#BC5A38"
    });
}
