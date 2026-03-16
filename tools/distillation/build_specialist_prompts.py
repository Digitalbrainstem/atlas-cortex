#!/usr/bin/env python3
"""Generate diverse specialist prompts for domain LoRA distillation.

Creates separate JSONL files for coding, reasoning, math, and medical domains
with high diversity, multiple difficulty levels, and reasoning-chain instructions.

Usage:
    python3 tools/distillation/build_specialist_prompts.py \
        --output-dir data/distillation/specialist \
        --coding 2500 --reasoning 2000 --math 2000 --medical 1500
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

random.seed(42)


# =============================================================================
# CODING DOMAIN
# =============================================================================

CODING_LANGUAGES = {
    "python": 0.35,
    "javascript": 0.15,
    "typescript": 0.10,
    "sql": 0.12,
    "bash": 0.08,
    "rust": 0.05,
    "go": 0.05,
    "html_css": 0.05,
    "java": 0.03,
    "cpp": 0.02,
}

CODING_TASK_TYPES = {
    "write_function": 0.25,
    "explain_code": 0.15,
    "debug_fix": 0.15,
    "architecture": 0.10,
    "refactor": 0.08,
    "compare": 0.07,
    "concept": 0.10,
    "best_practice": 0.05,
    "convert": 0.05,
}

# Difficulty: beginner(25%), intermediate(50%), advanced(25%)
CODING_PROMPTS_STATIC = [
    # === PYTHON ===
    # Write function
    {"prompt": "Write a Python function that takes a list of integers and returns the second largest value. Handle edge cases.", "lang": "python", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a Python function to flatten a nested list of arbitrary depth.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a Python decorator that caches function results with a TTL (time-to-live) expiration.", "lang": "python", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Write a Python function to validate an email address using regex. Explain each part of the pattern.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a Python context manager that temporarily changes the working directory and restores it on exit.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a Python function to merge two sorted lists into a single sorted list without using built-in sort.", "lang": "python", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write an async Python function that fetches multiple URLs concurrently and returns results as they complete.", "lang": "python", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Write a Python generator that yields Fibonacci numbers indefinitely.", "lang": "python", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a Python function that takes a string and returns all possible permutations.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a Python class implementing a thread-safe singleton pattern.", "lang": "python", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Write a Python function to detect cycles in a linked list using Floyd's algorithm.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a Python function to parse a simple arithmetic expression string like '3 + 5 * 2' respecting operator precedence.", "lang": "python", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Write a Python function that reads a CSV file and returns a list of dictionaries.", "lang": "python", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a Python function to implement binary search on a sorted list. Return the index or -1.", "lang": "python", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a Python dataclass for a 2D point that supports addition, distance calculation, and string representation.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a Python function that implements the LRU cache from scratch using a dict and doubly linked list.", "lang": "python", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Write a Python function to count word frequencies in a text, ignoring punctuation and case.", "lang": "python", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a Python function that groups a list of strings by their first letter into a dictionary.", "lang": "python", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a retry decorator in Python with exponential backoff and configurable max retries.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a Python function to find all subsets of a given set.", "lang": "python", "task": "write_function", "difficulty": "intermediate"},

    # Debug / Fix
    {"prompt": "This Python code has a bug. Find and fix it:\n```python\ndef avg(nums):\n    return sum(nums) / len(nums)\n\nresult = avg([])\n```", "lang": "python", "task": "debug_fix", "difficulty": "beginner"},
    {"prompt": "This Python code leaks memory. Identify the issue and fix it:\n```python\nclass Node:\n    def __init__(self, parent=None):\n        self.parent = parent\n        self.children = []\n        if parent:\n            parent.children.append(self)\n```", "lang": "python", "task": "debug_fix", "difficulty": "advanced"},
    {"prompt": "This async Python code has a race condition. Find and fix it:\n```python\ncounter = 0\nasync def increment():\n    global counter\n    val = counter\n    await asyncio.sleep(0)\n    counter = val + 1\n```", "lang": "python", "task": "debug_fix", "difficulty": "advanced"},
    {"prompt": "This Python code doesn't produce the expected output. Find the bug:\n```python\ndef remove_dupes(lst):\n    for item in lst:\n        if lst.count(item) > 1:\n            lst.remove(item)\n    return lst\n```", "lang": "python", "task": "debug_fix", "difficulty": "intermediate"},
    {"prompt": "Fix this Python code that's supposed to read a file safely:\n```python\ndef read_file(path):\n    f = open(path)\n    data = f.read()\n    return data\n```", "lang": "python", "task": "debug_fix", "difficulty": "beginner"},

    # Explain
    {"prompt": "Explain what this Python code does step by step:\n```python\nresult = {k: v for k, v in sorted(d.items(), key=lambda x: x[1], reverse=True)[:5]}\n```", "lang": "python", "task": "explain_code", "difficulty": "intermediate"},
    {"prompt": "Explain Python's GIL (Global Interpreter Lock). What problems does it cause and how do you work around it?", "lang": "python", "task": "explain_code", "difficulty": "advanced"},
    {"prompt": "Explain the difference between `is` and `==` in Python with examples.", "lang": "python", "task": "explain_code", "difficulty": "beginner"},
    {"prompt": "Explain Python's `__slots__` and when you should use it.", "lang": "python", "task": "explain_code", "difficulty": "intermediate"},
    {"prompt": "Explain how Python's garbage collector works. What's reference counting vs generational GC?", "lang": "python", "task": "explain_code", "difficulty": "advanced"},

    # === JAVASCRIPT / TYPESCRIPT ===
    {"prompt": "Write a JavaScript function that debounces another function with a given delay.", "lang": "javascript", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain the difference between var, let, and const in JavaScript with examples.", "lang": "javascript", "task": "explain_code", "difficulty": "beginner"},
    {"prompt": "Write a TypeScript generic function that takes an array and a predicate, and returns the first matching element or undefined.", "lang": "typescript", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain JavaScript's event loop. How do setTimeout, Promises, and async/await interact?", "lang": "javascript", "task": "explain_code", "difficulty": "advanced"},
    {"prompt": "Write a JavaScript function that deep clones an object without using JSON.parse/stringify.", "lang": "javascript", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Fix this JavaScript code:\n```javascript\nfor (var i = 0; i < 5; i++) {\n  setTimeout(() => console.log(i), 100);\n}\n// Expected: 0,1,2,3,4 but prints 5,5,5,5,5\n```", "lang": "javascript", "task": "debug_fix", "difficulty": "intermediate"},
    {"prompt": "Write a TypeScript interface for a REST API response that includes pagination metadata and generic data array.", "lang": "typescript", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain JavaScript closures with 3 practical examples.", "lang": "javascript", "task": "explain_code", "difficulty": "intermediate"},
    {"prompt": "Write a JavaScript Promise.all implementation from scratch.", "lang": "javascript", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "What is the difference between == and === in JavaScript? When should you use each?", "lang": "javascript", "task": "compare", "difficulty": "beginner"},
    {"prompt": "Write a TypeScript function that validates and parses a configuration object using discriminated unions.", "lang": "typescript", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Explain prototypal inheritance in JavaScript. How does it differ from class-based inheritance?", "lang": "javascript", "task": "explain_code", "difficulty": "advanced"},

    # === SQL ===
    {"prompt": "Write a SQL query to find the top 5 customers by total order amount, including their name and total.", "lang": "sql", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Explain the difference between INNER JOIN, LEFT JOIN, RIGHT JOIN, and FULL OUTER JOIN with examples.", "lang": "sql", "task": "explain_code", "difficulty": "intermediate"},
    {"prompt": "Write a SQL query using window functions to rank employees by salary within each department.", "lang": "sql", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a SQL query to find customers who made purchases every month in the last year.", "lang": "sql", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Explain SQL query optimization. What makes a query slow and how do indexes help?", "lang": "sql", "task": "explain_code", "difficulty": "intermediate"},
    {"prompt": "Write a SQL query to pivot monthly sales data from rows to columns.", "lang": "sql", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "What is the difference between WHERE and HAVING in SQL? Give examples.", "lang": "sql", "task": "compare", "difficulty": "beginner"},
    {"prompt": "Write a recursive CTE in SQL to traverse a parent-child hierarchy (like an org chart).", "lang": "sql", "task": "write_function", "difficulty": "advanced"},
    {"prompt": "Design a database schema for an e-commerce system with users, products, orders, and reviews. Show the CREATE TABLE statements.", "lang": "sql", "task": "architecture", "difficulty": "intermediate"},
    {"prompt": "Explain SQL injection and how to prevent it with examples.", "lang": "sql", "task": "best_practice", "difficulty": "intermediate"},

    # === BASH ===
    {"prompt": "Write a bash script to find all files larger than 100MB in the current directory tree.", "lang": "bash", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a bash one-liner to find the 10 most common words in a text file.", "lang": "bash", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Write a bash script that monitors a log file and sends an alert when an error pattern appears.", "lang": "bash", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain the difference between single quotes, double quotes, and backticks in bash.", "lang": "bash", "task": "explain_code", "difficulty": "beginner"},
    {"prompt": "Write a bash function that safely creates a backup of a directory with timestamp naming and rotation.", "lang": "bash", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain pipe, redirection, and process substitution in bash with examples.", "lang": "bash", "task": "explain_code", "difficulty": "intermediate"},

    # === RUST ===
    {"prompt": "Write a Rust function that takes a vector of strings and returns the longest one. Handle empty vectors.", "lang": "rust", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Explain Rust's ownership system. What are ownership, borrowing, and lifetimes?", "lang": "rust", "task": "explain_code", "difficulty": "intermediate"},
    {"prompt": "Write a Rust implementation of a simple key-value store using HashMap with get, set, and delete methods.", "lang": "rust", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain the difference between Box, Rc, and Arc in Rust. When would you use each?", "lang": "rust", "task": "compare", "difficulty": "advanced"},
    {"prompt": "Write a Rust enum and impl to represent and evaluate simple arithmetic expressions.", "lang": "rust", "task": "write_function", "difficulty": "advanced"},

    # === GO ===
    {"prompt": "Write a Go function that reads a JSON file and unmarshals it into a struct.", "lang": "go", "task": "write_function", "difficulty": "beginner"},
    {"prompt": "Write a Go HTTP handler that accepts POST requests with JSON body and responds with validation errors.", "lang": "go", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain goroutines and channels in Go. How do they differ from threads and locks?", "lang": "go", "task": "explain_code", "difficulty": "intermediate"},
    {"prompt": "Write a Go function that uses sync.WaitGroup to fetch multiple URLs concurrently.", "lang": "go", "task": "write_function", "difficulty": "intermediate"},
    {"prompt": "Explain Go's interface system. How does it compare to interfaces in Java or TypeScript?", "lang": "go", "task": "compare", "difficulty": "intermediate"},

    # === ARCHITECTURE / CONCEPTS ===
    {"prompt": "Design a rate limiter for an API. Explain the algorithm, data structures, and implementation.", "lang": "python", "task": "architecture", "difficulty": "advanced"},
    {"prompt": "What is the difference between REST and GraphQL? When should you use each?", "lang": "python", "task": "compare", "difficulty": "intermediate"},
    {"prompt": "Explain microservices vs monolith architecture. What are the trade-offs?", "lang": "python", "task": "compare", "difficulty": "intermediate"},
    {"prompt": "Design a URL shortener service. Cover the API, database schema, and hash generation.", "lang": "python", "task": "architecture", "difficulty": "intermediate"},
    {"prompt": "Explain SOLID principles with a practical example for each.", "lang": "python", "task": "concept", "difficulty": "intermediate"},
    {"prompt": "What is CAP theorem? Explain with examples of real databases.", "lang": "python", "task": "concept", "difficulty": "advanced"},
    {"prompt": "Explain the Observer pattern. Give an implementation example and when to use it.", "lang": "python", "task": "concept", "difficulty": "intermediate"},
    {"prompt": "What is the difference between horizontal and vertical scaling? How do you decide which to use?", "lang": "python", "task": "compare", "difficulty": "intermediate"},
    {"prompt": "Explain how a load balancer works. What are the main algorithms?", "lang": "python", "task": "concept", "difficulty": "intermediate"},
    {"prompt": "Design a simple task queue system. How would you handle retries, dead letters, and ordering?", "lang": "python", "task": "architecture", "difficulty": "advanced"},
    {"prompt": "What is eventual consistency? How does it differ from strong consistency?", "lang": "python", "task": "concept", "difficulty": "advanced"},
    {"prompt": "Explain how JWT authentication works. What are the security considerations?", "lang": "python", "task": "concept", "difficulty": "intermediate"},
    {"prompt": "What is the difference between OAuth2 and OpenID Connect?", "lang": "python", "task": "compare", "difficulty": "advanced"},
    {"prompt": "Design a caching strategy for a web application. When would you use each cache level?", "lang": "python", "task": "architecture", "difficulty": "intermediate"},
    {"prompt": "Explain database transactions and ACID properties with examples.", "lang": "python", "task": "concept", "difficulty": "intermediate"},
]

CODING_PARAMETRIC = {
    "write_function": [
        "Write a {lang} function to {task}. Include error handling and edge cases.",
        "Implement {task} in {lang}. Show the complete solution with comments.",
        "Write a {lang} solution for: {task}. Explain your approach step by step.",
    ],
    "explain_code": [
        "Explain how {concept} works in {lang}. Give practical examples.",
        "What is {concept} in {lang}? When should you use it?",
        "Explain {concept} with a {lang} code example. Why does it matter?",
    ],
    "debug_fix": [
        "What are common {lang} mistakes when working with {concept}? Show examples and fixes.",
        "I'm getting an error with {concept} in {lang}. What are the most likely causes?",
    ],
}

CODING_TASKS = [
    "reverse a linked list", "implement a stack using arrays", "find the longest palindrome substring",
    "validate balanced parentheses", "implement a min-heap", "serialize and deserialize a binary tree",
    "find the shortest path in a graph (BFS)", "implement a trie for word search",
    "merge overlapping intervals", "find the median of two sorted arrays",
    "implement a simple HTTP client", "parse command line arguments",
    "read and process a JSON configuration file", "implement a simple pub/sub system",
    "create a thread pool", "implement a simple ORM for SQLite",
    "build a CLI tool that converts CSV to JSON", "implement a rate limiter using token bucket",
    "create a file watcher that detects changes", "implement a simple template engine",
    "build a basic key-value store with persistence", "create a middleware pipeline",
    "implement a simple event emitter", "build a password strength checker",
    "create a function to detect palindromes", "implement topological sort",
    "build a simple calculator REPL", "create a function to validate credit card numbers (Luhn)",
    "implement a circular buffer", "build a simple regex matcher for . and *",
    "create a function to compress and decompress strings (RLE)", "implement an A* pathfinding algorithm",
    "build a simple web scraper", "create a function to diff two text files",
    "implement connection pooling", "build a simple bloom filter",
]

CODING_CONCEPTS = [
    "closures", "generators", "decorators", "async/await", "type hints",
    "error handling", "memory management", "concurrency", "design patterns",
    "testing", "dependency injection", "immutability", "higher-order functions",
    "pattern matching", "streams", "iterators", "promises", "modules",
    "inheritance vs composition", "interfaces", "generics", "enums",
    "list comprehensions", "map/filter/reduce", "exception hierarchy",
    "virtual environments", "package management", "logging best practices",
    "database connections", "API versioning", "middleware", "serialization",
]


# =============================================================================
# REASONING / LOGIC DOMAIN
# =============================================================================

REASONING_PROMPTS_STATIC = [
    # Multi-step deduction
    {"prompt": "Alice is taller than Bob. Bob is taller than Carol. Dave is shorter than Carol but taller than Eve. Rank all five people from tallest to shortest. Show your reasoning.", "subcat": "deduction", "difficulty": "beginner"},
    {"prompt": "In a town, the barber shaves everyone who does not shave themselves. Does the barber shave himself? Analyze this paradox.", "subcat": "deduction", "difficulty": "advanced"},
    {"prompt": "Five houses in a row are painted different colors. The English person lives in the red house. The Spanish person owns a dog. Coffee is drunk in the green house. The green house is to the right of the white house. Who owns the fish? (Provide all clues needed and solve step by step.)", "subcat": "deduction", "difficulty": "advanced"},
    {"prompt": "If it rains, the ground gets wet. The ground is wet. Can we conclude it rained? Explain why or why not.", "subcat": "deduction", "difficulty": "beginner"},
    {"prompt": "Three boxes are labeled 'Apples', 'Oranges', and 'Mixed'. All labels are wrong. You can pick one fruit from one box. How do you determine the correct labels? Walk through the logic.", "subcat": "deduction", "difficulty": "intermediate"},

    # Logic puzzles
    {"prompt": "You have 8 identical-looking balls. One is heavier. Using a balance scale, what is the minimum number of weighings to find the heavy ball? Explain your strategy.", "subcat": "puzzle", "difficulty": "intermediate"},
    {"prompt": "A farmer needs to cross a river with a fox, a chicken, and a bag of grain. The boat holds only the farmer and one item. The fox will eat the chicken if left alone. The chicken will eat the grain if left alone. How does the farmer cross? Show all steps.", "subcat": "puzzle", "difficulty": "beginner"},
    {"prompt": "You meet two guards. One always tells the truth, one always lies. You don't know which is which. With one question, how do you find the correct door? Explain the logic.", "subcat": "puzzle", "difficulty": "intermediate"},
    {"prompt": "There are 100 lockers, all closed. Student 1 opens every locker. Student 2 toggles every 2nd locker. Student 3 toggles every 3rd, and so on up to student 100. Which lockers are open at the end? Explain the pattern.", "subcat": "puzzle", "difficulty": "intermediate"},
    {"prompt": "You have two ropes. Each takes exactly 1 hour to burn, but they don't burn at a uniform rate. How do you measure exactly 45 minutes? Explain step by step.", "subcat": "puzzle", "difficulty": "intermediate"},

    # Analogies
    {"prompt": "Complete this analogy and explain your reasoning: Surgeon is to scalpel as painter is to ___.", "subcat": "analogy", "difficulty": "beginner"},
    {"prompt": "How is debugging code similar to being a detective solving a crime? Draw at least 5 parallels.", "subcat": "analogy", "difficulty": "intermediate"},
    {"prompt": "Explain how the human immune system is analogous to a computer's security system.", "subcat": "analogy", "difficulty": "intermediate"},
    {"prompt": "Complete and explain: Democracy is to voting as science is to ___.", "subcat": "analogy", "difficulty": "beginner"},

    # Cause-effect
    {"prompt": "A city doubles its population in 10 years. Trace through at least 8 downstream effects this would have on infrastructure, economy, and quality of life.", "subcat": "cause_effect", "difficulty": "intermediate"},
    {"prompt": "If all bees disappeared tomorrow, trace the chain of consequences through the food system, economy, and ecosystem. Be specific.", "subcat": "cause_effect", "difficulty": "intermediate"},
    {"prompt": "A major tech company announces all its products will be open source. What are the short-term and long-term consequences? Consider multiple stakeholders.", "subcat": "cause_effect", "difficulty": "advanced"},

    # Temporal reasoning
    {"prompt": "Put these events in chronological order and explain the causal links between them: printing press, Protestant Reformation, rise of literacy, Scientific Revolution, Age of Exploration.", "subcat": "temporal", "difficulty": "intermediate"},
    {"prompt": "A project has these dependencies: Task C requires A and B. Task D requires B. Task E requires C and D. Tasks A and B can run in parallel. What is the critical path? What is the minimum completion time if each task takes 2 days?", "subcat": "temporal", "difficulty": "intermediate"},

    # Spatial reasoning
    {"prompt": "You're facing north. You turn right 90 degrees, walk forward, turn left 90 degrees, walk forward, turn around 180 degrees. What direction are you facing now? Show your reasoning.", "subcat": "spatial", "difficulty": "beginner"},
    {"prompt": "A cube is painted red on all sides, then cut into 27 smaller cubes (3x3x3). How many small cubes have: exactly 3 painted sides? Exactly 2? Exactly 1? Zero? Explain.", "subcat": "spatial", "difficulty": "intermediate"},

    # Ethical reasoning
    {"prompt": "A self-driving car must choose between hitting one pedestrian or swerving to hit three. Analyze this dilemma from utilitarian, deontological, and virtue ethics perspectives. Don't give a final answer — explain the reasoning frameworks.", "subcat": "ethical", "difficulty": "advanced"},
    {"prompt": "Should AI systems be allowed to make medical decisions without human oversight? Present arguments for and against, considering safety, efficiency, equity, and accountability.", "subcat": "ethical", "difficulty": "advanced"},

    # Probabilistic reasoning
    {"prompt": "A medical test is 99% accurate. A disease affects 1 in 10,000 people. If you test positive, what's the actual probability you have the disease? Walk through Bayes' theorem step by step.", "subcat": "probability", "difficulty": "advanced"},
    {"prompt": "You're on a game show with 3 doors. One has a car, two have goats. You pick door 1. The host opens door 3 (goat). Should you switch to door 2? Prove why using probability.", "subcat": "probability", "difficulty": "intermediate"},
    {"prompt": "If you shuffle a deck of cards, what's the probability that no card is in its original position? (The derangement problem.) Explain the approach.", "subcat": "probability", "difficulty": "advanced"},
]

REASONING_TEMPLATES = [
    "Given these premises: {premises}. What can we logically conclude? Show each step of your reasoning.",
    "Identify the logical fallacy in this argument: '{argument}'. Explain why it's flawed.",
    "Solve this logic puzzle step by step: {puzzle}",
    "Compare and contrast {concept_a} and {concept_b}. In what ways are they similar? Different? What insights does the comparison reveal?",
    "{scenario} — Analyze the situation from multiple perspectives and explain the likely outcomes.",
    "What would happen if {hypothetical}? Trace through the consequences logically.",
]

REASONING_PREMISES = [
    "All mammals breathe air. Whales are mammals. Whales live in water.",
    "No reptiles have feathers. Some dinosaurs had feathers. All birds have feathers.",
    "If it's a holiday, the office is closed. If the office is closed, no emails are sent. Today is a holiday.",
    "All squares are rectangles. All rectangles have four sides. Some four-sided shapes are not rectangles.",
    "Some fruits are red. All apples are fruits. Some apples are green.",
]

REASONING_FALLACIES = [
    "Everyone I know likes this movie, so it must be the best movie ever made.",
    "We should ban cars because they cause accidents, just like we should ban swimming pools because people drown in them.",
    "This medicine must work — my grandmother took it and she lived to be 95.",
    "You can't prove aliens don't exist, therefore they must exist.",
    "He's a successful businessman, so his views on climate science must be correct.",
    "We've always done it this way, so there's no reason to change.",
    "If we allow students to redo one test, soon they'll want to redo every test and no one will study.",
    "She's either with us or against us — there's no middle ground.",
]

REASONING_HYPOTHETICALS = [
    "humans could photosynthesize like plants",
    "the internet was never invented",
    "gravity was twice as strong",
    "everyone could read minds",
    "money was abolished worldwide",
    "humans lived to be 300 years old",
    "we discovered intelligent alien life tomorrow",
    "all languages merged into one global language",
    "fossil fuels ran out overnight",
    "AI became conscious and self-aware",
]


# =============================================================================
# MATH DOMAIN
# =============================================================================

MATH_PROMPTS_STATIC = [
    # Arithmetic / basic
    {"prompt": "A store is having a buy 2 get 1 free sale. Each item costs $15. If I want 7 items, how much do I pay? Show your work.", "subcat": "arithmetic", "difficulty": "beginner"},
    {"prompt": "If a recipe serves 4 people and I need to serve 10, how do I scale: 2 cups flour, 3/4 cup sugar, 1.5 tsp vanilla, and 6 eggs? Show each calculation.", "subcat": "arithmetic", "difficulty": "beginner"},
    {"prompt": "A car's fuel tank holds 50 liters. It gets 12 km/liter in the city and 18 km/liter on the highway. If a trip is 40% city and 60% highway, how far can a full tank take you?", "subcat": "arithmetic", "difficulty": "intermediate"},

    # Percentages / rates
    {"prompt": "An item costs $80. It's marked up 25%, then put on sale for 20% off. What's the final price? Is it the same as the original? Explain.", "subcat": "percentages", "difficulty": "intermediate"},
    {"prompt": "A population grows from 50,000 to 62,000 in 5 years. What is the annual growth rate? (Assume compound growth.) Show the formula and calculation.", "subcat": "percentages", "difficulty": "intermediate"},
    {"prompt": "You invest $10,000 at 6% annual interest compounded monthly. How much do you have after 5 years? Show the compound interest formula and each step.", "subcat": "percentages", "difficulty": "intermediate"},
    {"prompt": "A shirt is on sale for $34 after a 15% discount. What was the original price? Show your algebra.", "subcat": "percentages", "difficulty": "beginner"},

    # Algebra
    {"prompt": "Solve the system of equations: 2x + 3y = 12 and 4x - y = 5. Show every step.", "subcat": "algebra", "difficulty": "intermediate"},
    {"prompt": "A rectangular garden has a perimeter of 36 meters. Its length is 3 meters more than twice its width. Find the dimensions. Set up equations and solve.", "subcat": "algebra", "difficulty": "intermediate"},
    {"prompt": "Solve the quadratic equation: x² - 5x + 6 = 0 using both factoring and the quadratic formula. Verify your answers.", "subcat": "algebra", "difficulty": "intermediate"},
    {"prompt": "If f(x) = 3x² - 2x + 1, find f(4), f(-1), and f(0). Then find x when f(x) = 22.", "subcat": "algebra", "difficulty": "intermediate"},
    {"prompt": "A train travels at speed x km/h. A car travels 20 km/h faster. They cover the same distance in 3 hours and 2 hours respectively. Find x.", "subcat": "algebra", "difficulty": "intermediate"},

    # Geometry
    {"prompt": "A circular pizza has a 14-inch diameter. It's cut into 8 equal slices. What is the area of each slice? What about the arc length of each slice's crust? Show work.", "subcat": "geometry", "difficulty": "intermediate"},
    {"prompt": "A room is 12 feet long, 10 feet wide, and 8 feet high. How much paint do I need for all four walls (not ceiling or floor) if one gallon covers 400 square feet? Account for a 3x7 foot door and two 3x4 foot windows.", "subcat": "geometry", "difficulty": "intermediate"},
    {"prompt": "Prove that the sum of angles in any triangle is 180 degrees. Use two different methods.", "subcat": "geometry", "difficulty": "advanced"},
    {"prompt": "A cone has a radius of 5 cm and a slant height of 13 cm. Find the height, volume, and lateral surface area. Show all formulas used.", "subcat": "geometry", "difficulty": "intermediate"},
    {"prompt": "A ladder 10 meters long leans against a wall. Its base is 6 meters from the wall. How high up the wall does it reach? If the base slides out 2 more meters, how much does the top drop?", "subcat": "geometry", "difficulty": "intermediate"},

    # Probability / statistics
    {"prompt": "In a class of 30 students, 18 play soccer, 15 play basketball, and 10 play both. How many play neither? Draw a Venn diagram description and solve.", "subcat": "probability", "difficulty": "intermediate"},
    {"prompt": "You roll two fair dice. What's the probability that: (a) the sum is 7, (b) at least one die shows 6, (c) both show the same number? Show the sample space reasoning.", "subcat": "probability", "difficulty": "intermediate"},
    {"prompt": "A dataset has values: 12, 15, 18, 18, 20, 22, 25, 28, 30, 45. Calculate the mean, median, mode, range, variance, and standard deviation. Show each calculation.", "subcat": "statistics", "difficulty": "intermediate"},
    {"prompt": "Explain the Central Limit Theorem. Why is it important? Give a real-world example where it applies.", "subcat": "statistics", "difficulty": "advanced"},
    {"prompt": "A bag has 5 red and 3 blue marbles. You draw 2 without replacement. What's the probability both are red? What about one of each? Use tree diagrams.", "subcat": "probability", "difficulty": "intermediate"},
    {"prompt": "A normally distributed variable has mean 100 and standard deviation 15. What percentage of values fall between 85 and 115? Between 70 and 130? Use the empirical rule and explain.", "subcat": "statistics", "difficulty": "intermediate"},

    # Calculus basics
    {"prompt": "Find the derivative of f(x) = 3x⁴ - 2x³ + 5x - 7. Then find the equation of the tangent line at x = 1.", "subcat": "calculus", "difficulty": "intermediate"},
    {"prompt": "Find the area under the curve y = x² from x = 0 to x = 3 using integration. Verify with the power rule.", "subcat": "calculus", "difficulty": "intermediate"},
    {"prompt": "Explain what a derivative represents both mathematically and intuitively. Give 3 real-world examples of derivatives.", "subcat": "calculus", "difficulty": "beginner"},
    {"prompt": "A ball is thrown upward with height h(t) = -16t² + 64t + 5 feet. When does it reach max height? What is the max height? When does it hit the ground?", "subcat": "calculus", "difficulty": "intermediate"},

    # Word problems
    {"prompt": "A tank fills in 6 hours with pipe A and 8 hours with pipe B. A drain empties it in 12 hours. If all three are open, how long to fill the tank? Show the rate approach.", "subcat": "word_problem", "difficulty": "intermediate"},
    {"prompt": "Two cars leave the same point at the same time. One drives north at 60 mph and the other drives east at 80 mph. After 2 hours, how far apart are they? Show the geometry.", "subcat": "word_problem", "difficulty": "intermediate"},
    {"prompt": "You have $12,000 to invest. Part goes into a 4% account and the rest into a 7% account. If total annual interest is $660, how much is in each account?", "subcat": "word_problem", "difficulty": "intermediate"},
    {"prompt": "A plane flies 600 miles with a tailwind in 2 hours. The return trip against the wind takes 3 hours. Find the plane's speed in still air and the wind speed.", "subcat": "word_problem", "difficulty": "intermediate"},

    # Estimation / Fermi
    {"prompt": "Estimate how many piano tuners there are in Chicago. Walk through your reasoning using Fermi estimation.", "subcat": "estimation", "difficulty": "advanced"},
    {"prompt": "How many tennis balls can fit inside this room (assume 15x12x8 feet)? Show your estimation approach.", "subcat": "estimation", "difficulty": "intermediate"},
    {"prompt": "Estimate the total length of all roads in the United States. Show your reasoning.", "subcat": "estimation", "difficulty": "advanced"},

    # Discrete math
    {"prompt": "How many different 4-character passwords can be made using uppercase letters and digits? What if no character can repeat?", "subcat": "discrete", "difficulty": "intermediate"},
    {"prompt": "Explain the difference between permutations and combinations with examples. When do you use each?", "subcat": "discrete", "difficulty": "beginner"},
    {"prompt": "Prove by induction that 1 + 2 + 3 + ... + n = n(n+1)/2.", "subcat": "discrete", "difficulty": "intermediate"},

    # Unit conversion / practical
    {"prompt": "Convert 72°F to Celsius, then to Kelvin. Show the formulas.", "subcat": "conversion", "difficulty": "beginner"},
    {"prompt": "A recipe calls for 200g of flour. I only have a measuring cup (1 cup = 120g for flour). How many cups do I need? What about 150ml of milk if 1 cup = 240ml?", "subcat": "conversion", "difficulty": "beginner"},
    {"prompt": "Light travels at 3×10⁸ m/s. How long does it take sunlight to reach Earth (150 million km)? Express in minutes.", "subcat": "conversion", "difficulty": "beginner"},
]

MATH_TEMPLATES = [
    "Solve this step by step, showing all work: {problem}",
    "A student solved this problem and got {wrong_answer}. Find and explain the error:\n{problem}",
    "Solve this problem two different ways and verify the answers match: {problem}",
    "Explain the concept of {concept} with 3 examples of increasing difficulty.",
]

MATH_WORD_PROBLEMS = [
    "a store marks up products 40% then gives employees a 25% discount. What effective markup does an employee pay?",
    "three friends split a bill. The bill is $87.50 plus 20% tip, split equally. How much does each person pay?",
    "a car depreciates 15% per year. If it starts at $35,000, what is it worth after 3 years?",
    "a runner does a 5K in 22 minutes. What is their pace in minutes per mile? What speed in mph?",
    "you mix 2 liters of 30% juice with 3 liters of 60% juice. What percentage is the mixture?",
    "a pool is 25m × 12m × 2m. How many liters of water does it hold? If the fill rate is 500 liters/minute, how long to fill?",
    "a cylindrical water tank has diameter 4 feet and height 6 feet. How many gallons does it hold? (1 cubic foot = 7.48 gallons)",
    "you're tiling a 10×12 foot floor with 6×6 inch tiles. How many tiles do you need? If each tile costs $1.25, what's the total?",
]


# =============================================================================
# MEDICAL / HEALTH DOMAIN
# =============================================================================

MEDICAL_PROMPTS_STATIC = [
    # Symptoms → when to see a doctor
    {"prompt": "What are the warning signs of a stroke? What should someone do immediately if they suspect a stroke?", "subcat": "emergency", "difficulty": "intermediate"},
    {"prompt": "I've had a headache for 3 days. When should a persistent headache be a cause for concern? What symptoms warrant seeing a doctor?", "subcat": "symptoms", "difficulty": "beginner"},
    {"prompt": "What are the differences between a cold, the flu, and COVID-19? How can you tell them apart?", "subcat": "symptoms", "difficulty": "intermediate"},
    {"prompt": "What are the signs of dehydration in adults vs children? When does dehydration become a medical emergency?", "subcat": "symptoms", "difficulty": "intermediate"},
    {"prompt": "What are the warning signs of appendicitis? How is it different from general stomach pain?", "subcat": "symptoms", "difficulty": "intermediate"},
    {"prompt": "What does chest pain mean? What are the different causes, and which ones require immediate medical attention?", "subcat": "symptoms", "difficulty": "intermediate"},
    {"prompt": "What are the symptoms of a concussion? When should someone go to the ER after hitting their head?", "subcat": "symptoms", "difficulty": "intermediate"},
    {"prompt": "What causes chronic fatigue? What conditions should a doctor check for if someone is always tired?", "subcat": "symptoms", "difficulty": "intermediate"},

    # First aid
    {"prompt": "Someone is choking and can't breathe. Walk through the complete first aid response for adults, children, and infants.", "subcat": "first_aid", "difficulty": "intermediate"},
    {"prompt": "How do you treat a second-degree burn at home? When does a burn need medical attention?", "subcat": "first_aid", "difficulty": "beginner"},
    {"prompt": "What should a basic home first aid kit contain? Explain when you'd use each item.", "subcat": "first_aid", "difficulty": "beginner"},
    {"prompt": "Someone is having a severe allergic reaction (anaphylaxis). What are the symptoms and what should bystanders do?", "subcat": "first_aid", "difficulty": "intermediate"},
    {"prompt": "How do you properly clean and bandage a deep cut? What signs indicate it needs stitches?", "subcat": "first_aid", "difficulty": "beginner"},
    {"prompt": "What do you do if someone is having a seizure? What are common myths about seizure first aid?", "subcat": "first_aid", "difficulty": "intermediate"},
    {"prompt": "Explain CPR basics for someone who has never learned. Cover adult and child differences.", "subcat": "first_aid", "difficulty": "intermediate"},
    {"prompt": "How do you treat a nosebleed? When should a nosebleed be considered a medical emergency?", "subcat": "first_aid", "difficulty": "beginner"},

    # Medications
    {"prompt": "What is the difference between ibuprofen (Advil), acetaminophen (Tylenol), and aspirin? When should each be used? What are the risks?", "subcat": "medications", "difficulty": "intermediate"},
    {"prompt": "How do antibiotics work? Why is antibiotic resistance a problem? What can individuals do to help?", "subcat": "medications", "difficulty": "intermediate"},
    {"prompt": "What are common over-the-counter allergy medications? How do antihistamines vs decongestants work?", "subcat": "medications", "difficulty": "beginner"},
    {"prompt": "What does 'take with food' mean for medication? Why do some medicines need to be taken on an empty stomach?", "subcat": "medications", "difficulty": "beginner"},
    {"prompt": "What are statins? How do they work, what are common side effects, and why are they so widely prescribed?", "subcat": "medications", "difficulty": "intermediate"},
    {"prompt": "Explain how vaccines work. What's the difference between mRNA, viral vector, and traditional vaccines?", "subcat": "medications", "difficulty": "intermediate"},
    {"prompt": "What are blood thinners and why are they prescribed? What precautions should someone on blood thinners take?", "subcat": "medications", "difficulty": "intermediate"},
    {"prompt": "What are the common side effects of long-term NSAID use? Who should avoid NSAIDs?", "subcat": "medications", "difficulty": "intermediate"},

    # Nutrition
    {"prompt": "Explain macronutrients (protein, carbs, fat) and micronutrients (vitamins, minerals). What does a balanced diet look like?", "subcat": "nutrition", "difficulty": "beginner"},
    {"prompt": "What are the health effects of too much sodium? How much is recommended daily? What foods are high in sodium?", "subcat": "nutrition", "difficulty": "beginner"},
    {"prompt": "Explain the glycemic index. How does it affect blood sugar? Why does it matter for diabetics and non-diabetics?", "subcat": "nutrition", "difficulty": "intermediate"},
    {"prompt": "What vitamins and minerals do vegetarians and vegans need to pay special attention to? What are good plant-based sources?", "subcat": "nutrition", "difficulty": "intermediate"},
    {"prompt": "How does fiber affect digestion and health? What are soluble vs insoluble fiber? How much should adults get daily?", "subcat": "nutrition", "difficulty": "beginner"},
    {"prompt": "What is intermittent fasting? What does the scientific evidence say about its health effects?", "subcat": "nutrition", "difficulty": "intermediate"},

    # Mental health
    {"prompt": "What are the differences between feeling sad, clinical depression, and seasonal affective disorder? When should someone seek help?", "subcat": "mental_health", "difficulty": "intermediate"},
    {"prompt": "Explain the fight-or-flight response. How does chronic stress affect the body over time?", "subcat": "mental_health", "difficulty": "intermediate"},
    {"prompt": "What are evidence-based strategies for managing anxiety? Explain both immediate coping techniques and long-term approaches.", "subcat": "mental_health", "difficulty": "intermediate"},
    {"prompt": "What is cognitive behavioral therapy (CBT)? How does it work and what conditions does it treat?", "subcat": "mental_health", "difficulty": "intermediate"},
    {"prompt": "How does sleep deprivation affect mental and physical health? What is good sleep hygiene?", "subcat": "mental_health", "difficulty": "beginner"},

    # Anatomy / physiology
    {"prompt": "How does the heart work? Explain the four chambers, blood flow, and the cardiac cycle in simple terms.", "subcat": "anatomy", "difficulty": "beginner"},
    {"prompt": "Explain how the kidneys filter blood. What are the main things they regulate?", "subcat": "anatomy", "difficulty": "intermediate"},
    {"prompt": "How does the digestive system break down food from mouth to absorption? Cover each major organ's role.", "subcat": "anatomy", "difficulty": "intermediate"},
    {"prompt": "Explain how the immune system fights infection. What are the roles of white blood cells, antibodies, and T-cells?", "subcat": "anatomy", "difficulty": "intermediate"},
    {"prompt": "How do muscles work? Explain the difference between skeletal, smooth, and cardiac muscle.", "subcat": "anatomy", "difficulty": "beginner"},
    {"prompt": "How do the lungs exchange oxygen and carbon dioxide? What happens during an asthma attack?", "subcat": "anatomy", "difficulty": "intermediate"},
    {"prompt": "Explain how the endocrine system works. What are the major hormones and what do they control?", "subcat": "anatomy", "difficulty": "intermediate"},

    # Chronic conditions (patient education)
    {"prompt": "Explain Type 2 diabetes in plain language. What causes it, how is it managed, and what lifestyle changes help?", "subcat": "chronic", "difficulty": "intermediate"},
    {"prompt": "What is high blood pressure (hypertension)? Why is it called 'the silent killer'? What can people do about it?", "subcat": "chronic", "difficulty": "beginner"},
    {"prompt": "Explain asthma. What triggers attacks, how do inhalers work, and what's the difference between rescue and maintenance inhalers?", "subcat": "chronic", "difficulty": "intermediate"},
    {"prompt": "What is arthritis? Explain the difference between osteoarthritis and rheumatoid arthritis, including treatments.", "subcat": "chronic", "difficulty": "intermediate"},

    # Preventive health
    {"prompt": "What cancer screenings are recommended for adults? At what ages? What do mammograms, colonoscopies, and Pap smears check for?", "subcat": "preventive", "difficulty": "intermediate"},
    {"prompt": "What vaccines are recommended for adults (not just children)? Include boosters and travel vaccines.", "subcat": "preventive", "difficulty": "intermediate"},
    {"prompt": "What are the most effective ways to prevent heart disease? Discuss diet, exercise, and risk factors.", "subcat": "preventive", "difficulty": "intermediate"},
    {"prompt": "What does a routine blood test (CBC, metabolic panel) measure? What do the results mean?", "subcat": "preventive", "difficulty": "intermediate"},

    # Health literacy
    {"prompt": "What does 'benign' vs 'malignant' mean in a medical context?", "subcat": "health_literacy", "difficulty": "beginner"},
    {"prompt": "Explain what a CT scan, MRI, and X-ray each do. When is each one used?", "subcat": "health_literacy", "difficulty": "beginner"},
    {"prompt": "What does blood pressure reading '120/80' mean? What are the systolic and diastolic numbers?", "subcat": "health_literacy", "difficulty": "beginner"},
    {"prompt": "What is BMI? How is it calculated? What are its limitations as a health metric?", "subcat": "health_literacy", "difficulty": "beginner"},
    {"prompt": "What is the difference between a virus and a bacterial infection? Why do antibiotics only work on one?", "subcat": "health_literacy", "difficulty": "beginner"},
]

MEDICAL_TEMPLATES = [
    "Explain {condition} in simple terms. What causes it, what are the symptoms, and when should someone see a doctor?",
    "What are the common treatments for {condition}? Include both medical and lifestyle approaches.",
    "A friend says they have {symptom}. What could cause this? What questions would help narrow it down? When should they see a doctor?",
    "Compare {treatment_a} and {treatment_b} for treating {condition}. What are the pros and cons of each?",
    "What are the risk factors for {condition}? Which are modifiable and which aren't?",
]

MEDICAL_CONDITIONS = [
    "acid reflux (GERD)", "anemia", "carpal tunnel syndrome", "celiac disease",
    "chronic obstructive pulmonary disease (COPD)", "conjunctivitis (pink eye)",
    "eczema", "gallstones", "gout", "hemorrhoids", "irritable bowel syndrome (IBS)",
    "kidney stones", "Lyme disease", "mononucleosis", "osteoporosis",
    "plantar fasciitis", "pneumonia", "shingles", "sinusitis", "sleep apnea",
    "strep throat", "tendinitis", "thyroid disorders", "urinary tract infections (UTIs)",
    "vertigo",
]

MEDICAL_SYMPTOMS = [
    "persistent back pain", "recurring dizziness", "unexplained weight loss",
    "chronic joint pain", "frequent headaches", "shortness of breath during exercise",
    "persistent cough for over 2 weeks", "numbness in hands or feet",
    "blood in urine", "chronic insomnia", "heart palpitations",
    "swollen lymph nodes", "persistent bloating", "sudden vision changes",
    "ringing in the ears (tinnitus)",
]


# =============================================================================
# GENERATION LOGIC
# =============================================================================

def weighted_choice(options: dict[str, float]) -> str:
    items = list(options.keys())
    weights = list(options.values())
    return random.choices(items, weights=weights, k=1)[0]


def generate_coding_prompts(count: int) -> list[dict]:
    """Generate diverse coding prompts."""
    prompts = []

    # Add all static prompts
    for p in CODING_PROMPTS_STATIC:
        prompts.append({
            "prompt": p["prompt"],
            "category": "coding",
            "subcategory": f"coding_{p.get('task', 'general')}",
            "difficulty": p.get("difficulty", "intermediate"),
            "language": p.get("lang", "general"),
        })

    # Generate parametric prompts
    while len(prompts) < count:
        lang = weighted_choice(CODING_LANGUAGES)
        task_type = weighted_choice(CODING_TASK_TYPES)

        if task_type == "write_function" and CODING_TASKS:
            task_desc = random.choice(CODING_TASKS)
            template = random.choice(CODING_PARAMETRIC["write_function"])
            prompt_text = template.format(lang=lang, task=task_desc)
        elif task_type == "explain_code" and CODING_CONCEPTS:
            concept = random.choice(CODING_CONCEPTS)
            template = random.choice(CODING_PARAMETRIC["explain_code"])
            prompt_text = template.format(lang=lang, concept=concept)
        elif task_type == "debug_fix" and CODING_CONCEPTS:
            concept = random.choice(CODING_CONCEPTS)
            template = random.choice(CODING_PARAMETRIC["debug_fix"])
            prompt_text = template.format(lang=lang, concept=concept)
        else:
            concept = random.choice(CODING_CONCEPTS)
            prompt_text = f"Explain {concept} in {lang} with practical examples and best practices."

        difficulty = random.choices(
            ["beginner", "intermediate", "advanced"],
            weights=[0.25, 0.50, 0.25],
        )[0]

        prompts.append({
            "prompt": prompt_text,
            "category": "coding",
            "subcategory": f"coding_{task_type}",
            "difficulty": difficulty,
            "language": lang,
        })

    random.shuffle(prompts)
    return prompts[:count]


def generate_reasoning_prompts(count: int) -> list[dict]:
    """Generate diverse reasoning/logic prompts."""
    prompts = []

    for p in REASONING_PROMPTS_STATIC:
        prompts.append({
            "prompt": p["prompt"],
            "category": "reasoning",
            "subcategory": f"reasoning_{p.get('subcat', 'general')}",
            "difficulty": p.get("difficulty", "intermediate"),
        })

    while len(prompts) < count:
        variant = random.choice([
            "fallacy", "hypothetical", "premises", "analogy", "estimation",
        ])

        if variant == "fallacy":
            fallacy = random.choice(REASONING_FALLACIES)
            prompt_text = f"Identify the logical fallacy in this argument: \"{fallacy}\". Explain why it's flawed and give a corrected version."
            subcat = "reasoning_fallacy"
        elif variant == "hypothetical":
            hyp = random.choice(REASONING_HYPOTHETICALS)
            prompt_text = f"What would happen if {hyp}? Trace through at least 5 consequences, considering both immediate and long-term effects."
            subcat = "reasoning_hypothetical"
        elif variant == "premises":
            premise = random.choice(REASONING_PREMISES)
            prompt_text = f"Given these premises: {premise} — What can we logically conclude? What can we NOT conclude? Show your reasoning step by step."
            subcat = "reasoning_deduction"
        elif variant == "analogy":
            pairs = [
                ("software development", "building a house"),
                ("the brain", "a computer"),
                ("an ecosystem", "an economy"),
                ("learning a language", "learning to code"),
                ("the internet", "a highway system"),
                ("evolution", "machine learning"),
                ("democracy", "a marketplace"),
                ("the human body", "a city"),
                ("raising a child", "tending a garden"),
                ("a legal system", "rules of a game"),
            ]
            a, b = random.choice(pairs)
            prompt_text = f"How is {a} analogous to {b}? Identify at least 5 specific parallels, and note where the analogy breaks down."
            subcat = "reasoning_analogy"
        else:  # estimation
            estimations = [
                "how many words the average person speaks in a lifetime",
                "how much data the entire internet stores",
                "how many trees are on Earth",
                "how long it would take to count to a billion out loud",
                "how much water a typical household uses in a year",
                "how many commercial flights are in the air right now globally",
                "how many stars you can see on a clear night",
                "how much food the average person eats in a lifetime by weight",
            ]
            est = random.choice(estimations)
            prompt_text = f"Estimate {est}. Walk through your reasoning using Fermi estimation, stating each assumption clearly."
            subcat = "reasoning_estimation"

        difficulty = random.choices(
            ["beginner", "intermediate", "advanced"],
            weights=[0.20, 0.50, 0.30],
        )[0]

        prompts.append({
            "prompt": prompt_text,
            "category": "reasoning",
            "subcategory": subcat,
            "difficulty": difficulty,
        })

    random.shuffle(prompts)
    return prompts[:count]


def generate_math_prompts(count: int) -> list[dict]:
    """Generate diverse math prompts."""
    prompts = []

    for p in MATH_PROMPTS_STATIC:
        prompts.append({
            "prompt": p["prompt"],
            "category": "math",
            "subcategory": f"math_{p.get('subcat', 'general')}",
            "difficulty": p.get("difficulty", "intermediate"),
        })

    # Parametric word problems with numeric variety
    while len(prompts) < count:
        variant = random.choice([
            "word", "error_analysis", "concept", "calculation", "conversion",
        ])

        if variant == "word":
            problem = random.choice(MATH_WORD_PROBLEMS)
            prompt_text = f"Solve this step by step, showing all work: {problem}"
            subcat = "math_word_problem"
        elif variant == "error_analysis":
            # Generate a problem with wrong answer
            problems = [
                ("What is 15% of 240?", "30", "The student multiplied 240 × 0.15 but wrote 30 instead of 36"),
                ("Solve: 2(x + 3) = 10", "x = 2", "The student distributed incorrectly"),
                ("What is the area of a circle with radius 5?", "31.4", "The student confused circumference with area"),
                ("Simplify: (x² + 2x) / x", "x + 2", "The student didn't consider x ≠ 0"),
                ("What is 3/4 + 2/3?", "5/7", "The student added numerators and denominators separately"),
            ]
            prob, wrong, hint = random.choice(problems)
            prompt_text = f"A student was asked: '{prob}' and answered '{wrong}'. Is this correct? If not, find the error, explain the correct approach, and show the right answer."
            subcat = "math_error_analysis"
        elif variant == "concept":
            concepts = [
                "prime numbers", "the Pythagorean theorem", "logarithms", "exponential growth",
                "the order of operations (PEMDAS)", "absolute value", "scientific notation",
                "rate of change", "linear equations", "quadratic equations",
                "the Fibonacci sequence", "mean vs median vs mode",
                "standard deviation", "correlation vs causation",
                "combinations vs permutations", "probability distributions",
            ]
            concept = random.choice(concepts)
            prompt_text = f"Explain {concept} with examples. Start with the basic definition, then show 3 examples of increasing difficulty, then explain a real-world application."
            subcat = "math_concept"
        elif variant == "calculation":
            # Generate numeric problems with variety
            calc_types = [
                lambda: f"Calculate {random.randint(10,99)} × {random.randint(10,99)} mentally. Show your strategy.",
                lambda: f"What is {random.randint(1,9)}/{random.randint(2,12)} + {random.randint(1,9)}/{random.randint(2,12)}? Simplify your answer. Show work.",
                lambda: f"Convert {random.randint(1,999)}/{random.randint(2,20)} to a decimal and a percentage.",
                lambda: f"Find the GCD and LCM of {random.randint(12,144)} and {random.randint(12,144)}. Show the prime factorization method.",
                lambda: f"Simplify: √{random.choice([8,12,18,20,27,32,45,48,50,72,75,98,125,128,200])}. Show each step.",
                lambda: f"What is {random.randint(2,15)}! (factorial)? Show the calculation.",
            ]
            prompt_text = random.choice(calc_types)()
            subcat = "math_calculation"
        else:
            conversions = [
                f"Convert {random.randint(50,250)} kilometers to miles. Then convert {random.randint(100,500)} pounds to kilograms.",
                f"Convert {random.randint(0,100)}°C to Fahrenheit and Kelvin. Show the formulas.",
                f"How many seconds are in {random.randint(1,10)} weeks, {random.randint(1,6)} days, and {random.randint(1,23)} hours?",
                f"Convert {random.randint(1,50)} gallons to liters. Then convert {random.randint(100,1000)} milliliters to cups.",
                f"Convert {random.randint(1000,50000)} square feet to square meters and acres.",
            ]
            prompt_text = random.choice(conversions)
            subcat = "math_conversion"

        difficulty = random.choices(
            ["beginner", "intermediate", "advanced"],
            weights=[0.20, 0.50, 0.30],
        )[0]

        prompts.append({
            "prompt": prompt_text,
            "category": "math",
            "subcategory": subcat,
            "difficulty": difficulty,
        })

    random.shuffle(prompts)
    return prompts[:count]


def generate_medical_prompts(count: int) -> list[dict]:
    """Generate diverse medical/health prompts."""
    prompts = []

    for p in MEDICAL_PROMPTS_STATIC:
        prompts.append({
            "prompt": p["prompt"],
            "category": "medical",
            "subcategory": f"medical_{p.get('subcat', 'general')}",
            "difficulty": p.get("difficulty", "intermediate"),
        })

    while len(prompts) < count:
        variant = random.choice([
            "condition", "symptom", "comparison", "lifestyle", "risk",
        ])

        if variant == "condition":
            condition = random.choice(MEDICAL_CONDITIONS)
            template = random.choice(MEDICAL_TEMPLATES[:2])
            prompt_text = template.format(condition=condition)
            subcat = "medical_condition"
        elif variant == "symptom":
            symptom = random.choice(MEDICAL_SYMPTOMS)
            prompt_text = f"A friend mentions they have {symptom}. What could cause this? What questions would help narrow it down? When should they see a doctor?"
            subcat = "medical_symptom"
        elif variant == "comparison":
            comparisons = [
                ("generic medications", "brand-name medications", "common conditions"),
                ("urgent care", "emergency room", "non-life-threatening injuries"),
                ("physical therapy", "chiropractic care", "back pain"),
                ("aerobic exercise", "strength training", "overall health"),
                ("MRI", "CT scan", "soft tissue injuries"),
                ("therapy", "medication", "mild to moderate depression"),
                ("organic food", "conventional food", "nutrition"),
            ]
            a, b, context = random.choice(comparisons)
            prompt_text = f"Compare {a} and {b} for {context}. What are the pros and cons of each? When is each the better choice?"
            subcat = "medical_comparison"
        elif variant == "lifestyle":
            topics = [
                "How does regular exercise affect blood pressure? What types of exercise are best?",
                "What is the relationship between gut health and mental health? What does the research say?",
                "How does alcohol affect the body? What are the short-term and long-term effects of different drinking levels?",
                "What are the health effects of sitting for long periods? What can office workers do to mitigate them?",
                "How does stress affect the immune system? What are evidence-based stress reduction techniques?",
                "What is the relationship between sleep and weight management?",
                "How does caffeine affect the body? What are the benefits and risks of coffee consumption?",
                "What are the health benefits of walking 10,000 steps per day? Is this target evidence-based?",
                "How does screen time affect eye health and sleep quality? What are practical guidelines?",
                "What is the relationship between hydration and cognitive performance?",
            ]
            prompt_text = random.choice(topics)
            subcat = "medical_lifestyle"
        else:
            condition = random.choice(MEDICAL_CONDITIONS)
            prompt_text = f"What are the risk factors for {condition}? Which are modifiable (can be changed) and which aren't? What prevention strategies are recommended?"
            subcat = "medical_risk_factors"

        difficulty = random.choices(
            ["beginner", "intermediate", "advanced"],
            weights=[0.30, 0.50, 0.20],
        )[0]

        prompts.append({
            "prompt": prompt_text,
            "category": "medical",
            "subcategory": subcat,
            "difficulty": difficulty,
        })

    random.shuffle(prompts)
    return prompts[:count]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build specialist prompt corpus for domain LoRAs")
    parser.add_argument("--output-dir", default="data/distillation/specialist")
    parser.add_argument("--coding", type=int, default=2500, help="Number of coding prompts")
    parser.add_argument("--reasoning", type=int, default=2000, help="Number of reasoning prompts")
    parser.add_argument("--math", type=int, default=2000, help="Number of math prompts")
    parser.add_argument("--medical", type=int, default=1500, help="Number of medical prompts")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generators = {
        "coding": (generate_coding_prompts, args.coding),
        "reasoning": (generate_reasoning_prompts, args.reasoning),
        "math": (generate_math_prompts, args.math),
        "medical": (generate_medical_prompts, args.medical),
    }

    total = 0
    for domain, (gen_func, count) in generators.items():
        prompts = gen_func(count)

        # Assign IDs
        for i, p in enumerate(prompts):
            p["id"] = f"{domain}_{i:05d}"

        out_file = output_dir / f"prompts_{domain}.jsonl"
        with open(out_file, "w") as f:
            for p in prompts:
                f.write(json.dumps(p) + "\n")

        # Stats
        subcats = {}
        difficulties = {}
        for p in prompts:
            sub = p.get("subcategory", "unknown")
            diff = p.get("difficulty", "unknown")
            subcats[sub] = subcats.get(sub, 0) + 1
            difficulties[diff] = difficulties.get(diff, 0) + 1

        print(f"\n{'='*60}")
        print(f"  {domain.upper()}: {len(prompts)} prompts → {out_file}")
        print(f"{'='*60}")
        print("  Difficulty distribution:")
        for d in ["beginner", "intermediate", "advanced"]:
            pct = 100 * difficulties.get(d, 0) / len(prompts)
            print(f"    {d}: {difficulties.get(d, 0)} ({pct:.0f}%)")
        print("  Subcategories:")
        for sub, cnt in sorted(subcats.items(), key=lambda x: -x[1]):
            print(f"    {sub}: {cnt}")

        total += len(prompts)

    print(f"\n{'='*60}")
    print(f"  TOTAL: {total} specialist prompts across {len(generators)} domains")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
