const API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate";

const RESOURCES_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/resources";

const allocationForm = document.getElementById("allocationForm");
const resultBox = document.getElementById("result");

allocationForm.addEventListener("submit", async function (event) {
    event.preventDefault();

    const requestId = document.getElementById("requestId").value.trim();
    const resourceType = document.getElementById("resourceType").value;
    const location = document.getElementById("location").value.trim();
    const priority = Number(document.getElementById("priority").value);

    if (!requestId || !resourceType || !location || !priority) {
        showResult("Please fill in all fields.", false);
        return;
    }

    showResult("Connecting to AWS and processing request...", true);

    try {
        const response = await fetch(API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                request_id: requestId,
                resource_type: resourceType,
                location: location,
                priority: priority
            })
        });

        const data = await response.json();

        if (response.ok) {
            showResult(
                `
                <strong>Resource Allocated Successfully!</strong>
                <br><br>
                Request ID: ${data.request_id || requestId}
                <br>
                Resource ID: ${data.resource_id || "N/A"}
                <br>
                Allocation ID: ${data.allocation_id || "N/A"}
                <br>
                Status: ${data.status || "ALLOCATED"}
                `,
                true
            );

            loadResources();
        } else {
            showResult(
                data.message || "No suitable resource available.",
                false
            );

            loadResources();
        }

    } catch (error) {
        console.error("AWS API Error:", error);

        showResult(
            "Unable to connect to AWS API. Please try again.",
            false
        );
    }
});


async function loadResources() {

    try {

        const response = await fetch(RESOURCES_API_URL);

        if (!response.ok) {
            throw new Error("Failed to load resources");
        }

        const resources = await response.json();

        updateDashboard(resources);

        updateResourceTable(resources);

    } catch (error) {

        console.error("Resource API Error:", error);

    }
}


function updateDashboard(resources) {

    const total = resources.length;

    const available = resources.filter(
        resource => resource.Available === true
    ).length;

    const allocated = total - available;

    document.getElementById("totalResources").textContent = total;
    document.getElementById("availableResources").textContent = available;
    document.getElementById("allocatedResources").textContent = allocated;
}


function updateResourceTable(resources) {

    const tableBody = document.getElementById("resourcesTable");

    if (!tableBody) {
        return;
    }

    tableBody.innerHTML = "";

    resources.forEach(resource => {

        const row = document.createElement("tr");

        const status = resource.Available
            ? "Available"
            : "Allocated";

        row.innerHTML = `
            <td>${resource.resource_id || "N/A"}</td>
            <td>${resource.Type || "N/A"}</td>
            <td>${resource.Location || "N/A"}</td>
            <td>
                <span class="${resource.Available ? "status-available" : "status-allocated"}">
                    ${status}
                </span>
            </td>
        `;

        tableBody.appendChild(row);

    });
}


function showResult(message, success) {

    resultBox.classList.remove("hidden");

    resultBox.classList.remove(
        "result-success",
        "result-error"
    );

    if (success) {
        resultBox.classList.add("result-success");
    } else {
        resultBox.classList.add("result-error");
    }

    resultBox.innerHTML = message;
}


loadResources();