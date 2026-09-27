document.addEventListener("DOMContentLoaded", () => {
	const yearElement = document.getElementById("copyright-year");

	if (yearElement) {
		yearElement.textContent = new Date().getFullYear();
	}
});

window.addEventListener("load", () => {
	if (typeof confetti !== "function") {
		return;
	}

	confetti({
		particleCount: 120,
		spread: 80,
		origin: {
			y: 0.6,
		},
	});
});
