document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENT SELECTION ---
    const card = document.getElementById('flashcard');
    const questionEl = document.getElementById('card-question');
    const answerEl = document.getElementById('card-answer');
    const progressText = document.getElementById('progress-text');
    const flipButton = document.getElementById('flip-button');
    const prevButton = document.getElementById('prev-button');
    const nextButton = document.getElementById('next-button');
    const mobilePrevButton = document.getElementById('mobile-prev-button');
    const mobileNextButton = document.getElementById('mobile-next-button');
    const filterToggle = document.getElementById('filter-toggle'); // New
    const filterOptions = document.getElementById('filter-options'); // New

    // --- NEW: Filter Element Selection ---
    const chapterFilter = document.getElementById('chapter-filter');
    const marksFilter = document.getElementById('marks-filter');

    // --- STATE VARIABLES ---
    let allFlashcards = [];   // This holds the original, full deck
    let filteredDeck = [];  // This holds the currently active deck
    let currentIndex = 0;

    marked.use(markedKatex({
        throwOnError: false,
        output: 'html' // Ensure output is HTML
    }));

    // --- DATA FETCHING ---
    fetch('flashcard_data.json')
        .then(response => response.json())
        .then(data => {
            allFlashcards = data;
            if (allFlashcards.length > 0) {
                populateFilters(); // New function call
                applyFilters();    // New function call to set the initial view
            } else {
                questionEl.textContent = "No flashcards found.";
            }
        })
        .catch(error => {
            console.error("Error loading flashcards:", error);
            questionEl.innerHTML = "Error: Could not load 'flashcard_data.json'.";
        });

    // --- NEW: FILTERING LOGIC ---
    function populateFilters() {
        // Get unique, sorted lists of chapters and marks
        const chapters = [...new Set(allFlashcards.map(c => c.chapter))].sort();
        const marks = [...new Set(allFlashcards.map(c => c.marks))].sort((a, b) => a - b);

        chapters.forEach(chapter => {
            const option = document.createElement('option');
            option.value = chapter;
            option.textContent = chapter;
            chapterFilter.appendChild(option);
        });

        marks.forEach(mark => {
            const option = document.createElement('option');
            option.value = mark;
            option.textContent = `${mark} Marks`;
            marksFilter.appendChild(option);
        });
    }

    function applyFilters() {
        const selectedChapter = chapterFilter.value;
        const selectedMarks = marksFilter.value;

        let tempDeck = [...allFlashcards]; // Start with the full deck

        // Filter by chapter if a specific chapter is selected
        if (selectedChapter !== 'all') {
            tempDeck = tempDeck.filter(card => card.chapter === selectedChapter);
        }

        // Filter by marks if a specific mark is selected
        if (selectedMarks !== 'all') {
            // Use parseInt because the value from the select is a string
            tempDeck = tempDeck.filter(card => card.marks === parseInt(selectedMarks, 10));
        }

        filteredDeck = tempDeck;
        currentIndex = 0; // Reset index to the start of the new filtered deck
        displayCard(); // Update the view with the new deck
    }
    // --- END NEW ---

    // --- CORE FUNCTIONS (Updated to use filteredDeck) ---
    function displayCard() {
        if (filteredDeck.length === 0) {
            questionEl.innerHTML = "<h2>No cards match your filter.</h2>";
            answerEl.innerHTML = "";
            progressText.textContent = "Card 0 of 0";
            return;
        }

        const currentCard = filteredDeck[currentIndex];
        questionEl.innerHTML = `<strong>Q:</strong> ${currentCard.question} (${currentCard.marks}m)`;

        let answerHTML = `<strong>A:</strong> ${marked.parse(currentCard.note || "")}`;

        if (currentCard.code && currentCard.code.trim() !== "") {
            const escapedCode = currentCard.code.replace(/</g, "&lt;").replace(/>/g, "&gt;");
            answerHTML += `<h3>Code:</h3><pre><code class="php">${escapedCode}</code></pre>`;
        }

        // Supports both the current multi-image "images" array and the older single-image
        // "image" string field, in case a previously-exported deck hasn't been regenerated.
        const imageList = Array.isArray(currentCard.images) ? currentCard.images
            : (currentCard.image ? [currentCard.image] : []);
        if (imageList.length > 0) {
            answerHTML += `<h3>Output:</h3>`;
            for (const img of imageList) {
                answerHTML += `<img src="images/${img}" alt="Output Image">`;
            }
        }

        answerEl.innerHTML = answerHTML;

        try { hljs.highlightAll(); } catch (e) { }

        progressText.textContent = `Card ${currentIndex + 1} of ${filteredDeck.length}`;
        card.classList.remove('is-flipped');
    }

    function flipCard() {
        card.classList.toggle('is-flipped');
    }

    // --- NAVIGATION LOGIC (Updated to use filteredDeck) ---
    function nextCard() {
        if (filteredDeck.length === 0) return;
        currentIndex = (currentIndex + 1) % filteredDeck.length;
        displayCard();
    }

    function prevCard() {
        if (filteredDeck.length === 0) return;
        currentIndex = (currentIndex - 1 + filteredDeck.length) % filteredDeck.length;
        displayCard();
    }

    // --- EVENT LISTENERS ---
    flipButton.addEventListener('click', flipCard);
    card.addEventListener('click', flipCard);
    nextButton.addEventListener('click', nextCard);
    prevButton.addEventListener('click', prevCard);

    mobilePrevButton.addEventListener('click', (e) => { e.stopPropagation(); prevCard(); });
    mobileNextButton.addEventListener('click', (e) => { e.stopPropagation(); nextCard(); });

    // --- NEW: Listen for changes on the filter dropdowns ---
    chapterFilter.addEventListener('change', applyFilters);
    marksFilter.addEventListener('change', applyFilters);
    filterToggle.addEventListener('click', () => {
        filterOptions.classList.toggle('is-open');
    });

    document.addEventListener('keydown', (e) => {
        if (filteredDeck.length === 0) return;
        if (e.code === 'Space') {
            e.preventDefault();
            flipCard();
        } else if (e.code === 'ArrowRight') {
            nextCard();
        } else if (e.code === 'ArrowLeft') {
            prevCard();
        }
    });
});
