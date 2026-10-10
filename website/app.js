const languageButton = document.querySelector(".lang-toggle");
const translatable = document.querySelectorAll("[data-zh][data-en]");
let language = localStorage.getItem("homemind-language") || "zh";

function applyLanguage(nextLanguage) {
  language = nextLanguage;
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.title = language === "zh" ? "HomeMind — 懂每一位家人的 AI" : "HomeMind — AI that knows your home";
  translatable.forEach((element) => {
    element.innerHTML = element.dataset[language];
  });
  languageButton.textContent = language === "zh" ? "EN" : "中";
  localStorage.setItem("homemind-language", language);
}

languageButton.addEventListener("click", () => applyLanguage(language === "zh" ? "en" : "zh"));
applyLanguage(language);

document.querySelector("#year").textContent = new Date().getFullYear();

document.querySelector(".copy-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  await navigator.clipboard.writeText(button.dataset.copy);
  const original = language === "zh" ? "复制" : "Copy";
  button.textContent = language === "zh" ? "已复制" : "Copied";
  window.setTimeout(() => { button.textContent = original; }, 1600);
});

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.12 });

document.querySelectorAll(".manifesto article, .feature, .steps li, .section-heading, .how-copy").forEach((element) => observer.observe(element));

const header = document.querySelector(".site-header");
window.addEventListener("scroll", () => header.classList.toggle("is-scrolled", window.scrollY > 24), { passive: true });

const scene = document.querySelector(".home-scene");
if (window.matchMedia("(pointer: fine) and (prefers-reduced-motion: no-preference)").matches) {
  scene.addEventListener("pointermove", (event) => {
    const bounds = scene.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width - 0.5;
    const y = (event.clientY - bounds.top) / bounds.height - 0.5;
    scene.style.setProperty("--pointer-x", `${x * 10}px`);
    scene.style.setProperty("--pointer-y", `${y * 10}px`);
  });
  scene.addEventListener("pointerleave", () => {
    scene.style.setProperty("--pointer-x", "0px");
    scene.style.setProperty("--pointer-y", "0px");
  });
}
