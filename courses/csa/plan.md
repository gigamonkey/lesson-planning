---
course: csa
title: AP Computer Science A
primary_reference: csa-ced
primary_outline: csa-plan
targets: csa-book, csa-ced
---

# Unit: Introduction

## What is programming? What is Java?

- Compile and run HelloWorld.java  (#ed5c)
- Add useful comments to code.  (#938b)
- Properly format Java code. (By hand.)  (#70e1)
- Name all the punctuation characters used in Java.  (#020c)
- Describe what a text editor is.  (#16fb)
- List the purpose or purposes of each punctuation character.  (#fcab)
- Use comments to temporarily disable chunks of code.  (#d5b6)
- Remove redundant comments from code  (#6581)

## Debugging and being stuck

- Distinguish between compile-time and run-time errors from an error message.  (#aabe)
- Describe the kinds of errors that can be detected without running the program.  (#08d8)
- Fix common syntax errors in Java code  (#3fc1)
- Describe some errors that cannot be detected until we run the program.  (#4c1e)
- Identify missing semicolons and extraneous semicolons  (#a33b)
- Describe the difference between compile-time and run-time errors  (#bb26)
- Distinguish between syntax & type errors.  (#c9ea)
- Distinguish between syntax, run-time, and logic errors.  (#e550)
- List some situations when Java will throw a runtime Exception  (#f6ad)

## Transitioning from AP CSP to AP CSA

## Java Development Environments

## Growth mindset and pair programming

# Unit: Values, variables, and expressions

## Values and variables

- Distinguish between declaring, initializing, assigning, and using a variable  (#456c)
- List the three primitive data types we will use in this course.  (#9d51)
- Describe the purpose of literal values in code.  (#b30b)
- Distinguish between primitive and reference types.  (#c7eb)
- Describe the kinds of values in, non-computer terms, represented by `int`, `double`, and `boolean`.  (#c979)
- Distinguish between variables and values.  (#f344)

## Text output

- Write code that reads user input with Scanner  (#0d9d)
- Explain the difference between string literals and variables  (#243f)
- Write code that uses string + with non-String values.  (#6887)
- Write code to generate output with print and println  (#735b)
- Write code that uses strong literals.  (#94fb)
- Write code that uses Scanner with System.in  (#993d)
- Write code that uses string concatenation with +  (#adbc)
- Write code that uses variables of type String  (#d3d3)
- State why we can use the String class anywhere.  (#efac)
- Write string literals with escape characters  (#f057)

## Arithmetic expressions

- Write arithmetic expressions that include variables  (#1cb1)
- Write expressions using +, -, *, and /  (#32e8)
- Write expressions using %  (#369f)
- State the value of expressions involving truncating division.  (#3b75)
- Explain the difference between int and double math.  (#5373)
- Trace the order of evaluation of expressions & statements.  (#8aba)
- State what happens if we divide an `int` value by zero.  (#99d4)
- State the type of arithmetic expressions.  (#ab99)
- Write expressions using arithmetic operators obeying precedence  (#c220)
- Explain 2-3 uses of the remainder operator  (#d181)

## Assignment statements

- Write code using += with ints and Strings.  (#1c9c)
- Evaluate code including assignment statements  (#67ee)
- Explain results of code that uses compound assignment operators  (#9690)
- State the value and type of mixed int/double expressions.  (#c44e)
- Translate code between compound operators and not (incl. ++ & --)  (#cb9a)
- Explain code that uses post-increment and decrement operators.  (#d3c5)

## Getting math right

- Describe the limitations of doubles  (#0bfe)
- Explain how ints & doubles are an approximation  (#30e1)
- Describe the mathematical effect of casting a `double` to an `int`.  (#4d33)
- Write expressions to round doubles to nearest int w/o Math.round  (#5051)
- Use casts to get mathematically appropriate answers.  (#7103)
- Explain why doubles are bad for money.  (#713f)
- Evaluate arithmetic expressions involving casts.  (#902e)
- Explain why the order of operations can affect the value of double comp.  (#9a3e)
- State the names and approximate values (and exact expression) of int max.  (#ab5e)
- Explain the two (?) ways int math only approximates real math.  (#bbca)
- Describe the limits of ints, doubles, and booleans.  (#d5d4)
- Explain integer overflow: both when & why it occurs.  (#e6fa)
- State when double contagion ("zombification") occurs.  (#f9f2)

# Unit: Methods

## Writing methods

- Write static methods that call other static methods.  (#40d8)
- Trace code that contains return statements  (#5d29)
- Write non-void methods that return values computed by arbitrary expressons.  (#6157)
- State the type of expressions involving method calls.  (#6717)
- Write methods that take arguments that they used to compute their result or to have the desired side effects.  (#8879)
- Create methods by extracting code  (#93f2)
- Identify methods that can be used in an expression based on return type  (#96d3)
- Write non-void methods that compute values.  (#a68e)
- Write correct method signatures given a name, return type, and arg types  (#aa85)
- State the name, return type, and argument types & names given a usage.  (#aaa9)
- Identify the correct method signature for a given method call.  (#ac4c)
- Write correct calls to a method given its signature  (#bb40)
- Explain the difference between returning a string and printing  (#bb4c)
- Trace the execution of code involving method calls.  (#ccfc)
- Write void methods that have external side effects  (#d053)
- Explain how method calls affect the flow of control.  (#d33c)
- Write a `void` method.  (#e386)

## APIs and Libraries

- Explain the purpose of import in Java  (#4048)
- Define "attribute"  (#9bee)
- Describe the relationship between classes, libraries, & APIs  (#b0ca)

## The `Math` class

- Write expressions to generate random numbers from a speci  (#002c)
- Write expressions using Math.random() to generate random numbers  (#15d5)
- Explain the difference between inclusive and exclusive bounds  (#56a0)
- State why the `Math` class can be used in any code.  (#ac1c)
- Write arithmetic expressions with Math.pow, Math.sqrt, and Math.abs  (#bd0a)

# Unit: Booleans and conditionals

## Building blocks of algorithms

- Describe "repetition"  (#0532)
- Describe the default flow of control in Java code.  (#1367)
- Express a real-world procedure as an algorithm in English.  (#1e48)
- Trace code that uses all three of sequencing, selection, and repetition.  (#29fb)
- Describe "selection"  (#6596)
- List the three kinds of control flow.  (#7f8c)
- Define "sequencing"  (#8349)
- Explain the relation between sequential evaluation and assignment  (#8bb2)
- Describe the three basic building blocks of algorithms  (#d09c)
- Explain how repetition changes control flow  (#d7b1)
- Explain how selection changes control flow  (#dbe4)
- Describe an algorithm that combines sequencing, selection, and repetition.  (#f5ad)
- Match "selection" with decisions and "repetition" with looping  (#fefc)

## Booleans and `if` statements

- Write one-way selections with if  (#0a44)
- Simplify mutually-exclusive consecutive ifs into chained else ifs  (#1d9a)
- Trace the evaluation of if, if/else, & if/else if/else statements  (#2ecc)
- Write two-way selections with if/else  (#49c8)
- Translate multi-way else if chains to mutually exclusive ifs  (#788e)
- Explain the role of booleans in selection & repetition  (#b26e)
- Write code that uses `if`/`else` statements to create two way branches.  (#b523)
- Trace code that uses nested `if` statements.  (#d766)
- Calculate the result of code with consecutive ifs vs else if  (#deae)
- Write code that uses `if`/`else if`/`else` chains to create multiway branches.  (#e021)
- Write multi-way selections with if/else if/else  (#f092)

## Where do booleans come from?

- Write expressions using the relational operators <, >, <=, >=  (#3071)
- Write code to test if a number is even  (#4525)
- Write if statements that use Math.random() to execute code with probability  (#45a4)
- Write expressions using relational operators with `int`s and `double`s  (#6810)
- Write code to test if a number (incl negative) is odd  (#72e6)
- Calculate the value of boolean expressions using &&, ||, and !  (#8c66)
- Write chained relational expressions with && as standard form  (#a2f0)
- Explain the use of short circuiting boolean operators  (#fd15)

## Manipulating boolean expressions

- Create truth tables for boolean expressions of 2 & 3 variables  (#671f)
- Apply De Morgan's laws to boolean expressions  (#8d09)
- List the rules of boolean algebra  (#9a77)
- Apply De Morgan's laws to expressions involving relational & equality operators  (#a284)
- Explain what it means for expressions to be equivalent  (#a9b7)
- Simplify boolean expressions (2 and 3 variables)  (#b0a1)

## `if` statement traps and pitfalls

- Explain why you should always use braces with if & else  (#65b9)
- Explain why else if is an (okay) violation of the always use braces rule  (#c395)
- Distinguish between = and == and !=  (#dc9b)
- Explain why we should never use == & != with booleans  (#eafc)

# Unit: Loops

## While loops

- State when a `while` loop will not run its body at all.  (#14d0)
- Check loops by checking their bounds.  (#2e82)
- Identify a syntactically correct while loop  (#36ed)
- Trace the execution of a while loop  (#4382f)
- Explain the structure of a while loop  (#5719)
- Identify the body of a while loop  (#62f8)
- Write code that uses a while loop  (#7100)
- Fix inadvertant infinite while loops  (#9351)
- Identify the condition of a while loop  (#a36e)
- List conditions that you might use in a while loop  (#c6ef)
- State when you might want an infinite loop  (#fbcd)
- Identify infinite while loops  (#fc0d)

## For loops

- Write a canonical reverse for loop that runs N times  (#16f2)
- Explain the rule of thumb for choosing between while & for loops  (#3612)
- Explain the structure of a regular for loop  (#5795)
- Define the initializer, condition, and updater in a for loop  (#6d12)
- Translate while loops to equivalent for loops and vice versa.  (#7a94)
- Write a canonical for loop that runs N times  (#ac4f)
- Trace the execution of a for loop including the 3 header clauses  (#d2ae)

## Basic loop algorithms

- Write a loop to do something with the digits of an int.  (#1adf)
- Write a counting loop  (#49b7)
- Write a maximizing/minimizing loop  (#4f34)
- Write a summing loop  (#bef5)
- Write a reducing loop  (#c090)

## Nested loops

## Analyzing loops

- Trace the execution of a nested loop.  (#3284)
- Compute how many times a for loop will run.  (#4fa8)
- Compute how many times the inner body of a nested for loop runs.  (#9a82)

# Unit: Arrays

## Creating and using arrays

- Distinguish between modifying an array variable and an array element  (#38aa)
- List the default values for array elements of different types of array.  (#4474)
- State the type of expression using array access.  (#5785)
- Write code to access elements of arrays  (#5d01)
- Debug index out of bounds errors.  (#5ece)
- State the initial value of elements of an array constructed with new  (#76a2)
- Write methods that modify a passed in array.  (#7ad9)
- State the two contexts where an array initializer may appear  (#7f51)
- Write code to declare and initialize an array with an array initializer  (#959a)
- Describe how arrays are represented in the heap  (#9aa7)
- State the relationship between the length of an array and its last valid index.  (#9d19)
- State the value of array access expressions  (#a30b)
- State the valid indexer of an array  (#aade)
- Write code that declares variables with array types  (#adcd)
- Write code that modifies mutable objects stored in an array.  (#c2d7)
- Describe how arrays of reference types are stored in the heap.  (#c638)
- Identify the component type of 1- and 2-d array types  (#d718)
- Write code to create an array with an array creation expression  (#d8db)
- Distinguish between an index variable and a value.  (#d8ef)
- Distinguish between the two ways of allocating an array with new  (#f5f7)
- Write code that uses the length property of an array  (#f6cf)

## Looping over arrays

- Distinguish regular for loops that can be written as enhanced for loops  (#312e)
- Write regular for loops to traverse arrays  (#899a)
- Write code using all the standard looping patterns over an array  (#ba96)
- Define "traversing" an array.  (#c229)
- Write enhanced for loops to traverse a 1-d array  (#c37e)
- Write for loops that modify array elements.  (#ca38)
- Write code to traverse an array.  (#cab7)
- Translate between regular and enhanced for loops  (#e0a7)
- Explain why code in an enhanced for loop can't modify the array  (#ff94)

## Array algorithms

- Write code to detect duplicate elements in an array  (#23c9)
- Write code to process consecutive pairs of elements in an array  (#6bb0)
- Write code to process all pairs of elements in an array, ordered  (#719b)
- Write a find index loop  (#acf6)
- Write an all/every loop  (#c6ba)
- Write code to reverse the elements in an array  (#c939)
- Write an any/some loop  (#d42c)
- Write code to rotate elements one position left or right  (#d4cb)
- Write a finding loop  (#eec9)
- Write code to process all pairs of elements in an array, unordered  (#f087)

## Two-dimensional arrays

- Explain how a 2D array is just a 1D array in disguise  (#2bb6)
- State the component type of a 2D array  (#418b)
- Explain how 2D arrays are stored in the heap  (#4df0)
- State the number of rows & columns in a row-major 2D array  (#5aab)
- Write code to initialize a 2D array variable with given dimensions  (#6d70)
- Distinguish between the element type & component type of 2D array  (#6fe2)
- Write code to initialize a 2D array with an array initializer  (#7a20)
- State the number of values in a 2D array  (#a3e6)
- Outline the difference between viewing a 2D array as a grid vs table  (#ab95)
- Write code to access individual elements of a 2D array  (#b263)
- Write code to access individual rows of a 2D array  (#cbf1)

## Two-dimensional array algorithms

- Write code using standard loop algorithms over rows & columns of 2D array  (#474d)
- Write code to process all elements of a 2D array w/ regular for loops  (#733b)
- Write code to process all elements of a 2D array w/ enhanced for loops  (#93e0)
- Write code to process cells and their neighbors in a 2D array  (#9dd3)
- Write code using standard loop algorithms over all elements of 2D array  (#ddb7)

# Unit: Strings

## Manipulating strings

- Define "immutable."  (#299b)
- Write code to extract a one-character string at an index  (#58fd)
- Write code that uses indexOf with substring to extract parts  (#6ca0)
- Write code that uses compareTo  (#7bde)
- Write code that builds up a String value with +=  (#953f)
- Explain the recipe for writing comparisons using compareTo  (#a52f)
- Write code that uses both 1-arg and 2-arg substring  (#c4d7)
- Calculate the String and int values returned by String method calls  (#eb6f)
- Write code that uses String length  (#fa78)

## Implementing string algorithms

- Write a loop over all fixed-size substrings of a String  (#389d)
- Write a loop over all the characters (as Strings) in a String  (#6e4e)
- Write a loop over all substrings of a String  (#7d55)
- Write a String accumulator loop  (#874c)
- Write a loop to reverse a String  (#b296)

# Unit: Classes

## Anatomy of a class

- Write a class based on an abstract description.  (#0179)
- Sketch diagram of how instances and classes relate in memory  (#4382d)
- Write code to declare a `public` class.  (#4d8d)
- Describe the relationship between class attributes, methods, and constructors  (#c982)
- Explain the difference between allocating & initializing an object.  (#fad5)

## Instance variables

- Define "encapsulation"  (#13df)
- Describe the two main ways instance variables are initialized.  (#2695)
- Define an object's "state"  (#5e2e)
- List the default values for member variables & types: int, double, boolean, etc. and references  (#6c09)
- State what happens if you invoke an instance method on null.  (#7ea7)
- Distinguish between when we have to use this. and when its optional  (#92c4)
- Explain why the this. idiom is necessary.  (#b6e4)
- State how to disambiguate a reference to a shadowed member variable  (#c087)
- List the default values for uninitialized instance variables  (#d248)
- Explain why instance variables should usually be private.  (#e025)
- Describe how instance variables are used by instance methods.  (#ef0b)
- Explain the purpose of the null value  (#efab)

## Constructors

- Identify which overloaded constructor will be invoked by a call.  (#0fad)
- Evaluate code involving pass by value and changes to local variables.  (#3362)
- Describe the role of a constructor  (#3915)
- Identify syntactically correct constructor signatures.  (#48fd)
- Trace the flow of code containing calls to constructors.  (#7373)
- Distinguish between parameters (variables) and arguments (values).  (#7c0e)
- Explain argument passing in terms of the call stack  (#8888)
- Write correct invocations of a constructor with new  (#925a)
- Explain the main job of every constructor  (#9b45)
- Explain how call by value works.  (#9c85)
- State the purpose of constructor parameters.  (#a8dc)
- Identify correct & incorrect uses of constructor invocation based on expected type.  (#c019)
- Write overloaded constructors that use this()  (#c843)
- Identify correct & incorrect calls to a constructor based on arg types.  (#f0a9)
- Write constructors that use the this. idiom.  (#f0f8)
- State what constructor a class has that doesn't contain an explicit constructor  (#f2a0)
- Describe the differences between methods and constructors.  (#f2fb)
- Write a class with at least one `public` constructor.  (#f783)

## Instance methods

- Write void methods that have internal side effects  (#0d0b)
- Identify which of several overloaded methods will be invoked by a call.  (#1c16)
- List the methods all classes inherit from java.lang.Object  (#1f43)
- Describe the purpose of inheritance (limited to Object)  (#6d02)
- Explain the difference between "pure" and side-effecting methods.  (#7448)
- Explain why non-void methods usually shouldn't have side effects  (#782d)
- Explain why you might write a getter but no setter  (#9bae)
- Write correct calls to instance methods on specific object  (#a283)
- Write correct calls to instance methods with implicit this.  (#b8d8)
- Write methods that modify an array held in an instance variable  (#bbe7)
- Write a class with an overridden toString  (#be65)
- Write getters & setters (accessors & mutators)  (#c6b8)
- Write code to call instance methods  (#d68a)
- Write correct overriding to String methods.  (#d9e7)
- Explain the relationship between + and toString  (#e906)

# Unit: Objects

## Connecting objects

- Write methods within a class that access `private` instance variables on multiple instances of the class.  (#20eb)
- State where you can use this as a variable  (#28d9)
- Explain where instance variables are stored in memory  (#420b)
- Explain why a constructor might make a copy of one of its arguments.  (#46a7)
- Explain how reference types are returned in terms of the call stack  (#5155)
- Write methods that modify a mutable argument  (#5cb2)
- Write code that passes this as an argument  (#5fa9)
- Explain why non-primitive types are passed by reference.  (#7a49)
- Use constructor calls in contexts other than assignments.  (#ca6e)
- Explain passing references as arguments in terms of the call stack  (#e8d2)
- Explain the consequences of passing mutable types by reference  (#f04d)

## Object equality

- Explain why classes often override equals  (#1f9a)
- Distinguish between the reference value & object data of an instance.  (#564b)
- Explain why its necessary to use equals with String objects.  (#5a6b)
- Define the meaning of == & != for primitive & reference values  (#7269)
- Calculate the value of == & != expressions with references including null  (#803a)
- Calculate the value of expressions using == & != with references  (#8c49)
- Explain what it means if two objects represent the same mutable object  (#9d6d)
- Explain why we can (and should) use equals with all reference types.  (#9e74)
- Explain why equals probably doesn't do what we want with arrays.  (#cfa1)
- Explain the difference between Object.equals & String.equals.  (#dcad)

## Some odds and ends

- Write correct calls to a static method from inside the class.  (#0a1a)
- Write a class that declares a `public final static` member variable.  (#11fa)
- State the proper modifiers for a variable holding a constant value  (#1299)
- Write correct calls to static & instance methods based on signatures.  (#2098)
- Distinguish between class (static) and instance variables  (#3f5e)
- Explain how to decide if a method should be public or private.  (#4bf8)
- Explain where you can access a private member of a class  (#4cbb)
- Determine whether a method can be static or not.  (#6128)
- List the two kinds of attributes in classes.  (#7dc9)
- Distinguish between local and member variables  (#8109)
- Write code that declares and uses static variables  (#8377)
- Write code to call static methods  (#9cab)
- Explain why constant values are often defined as static variables  (#9cef)
- Explain the scope of variables  (#a719)
- Explain why static methods can't access instance variables  (#b08a)
- Identify shadowed variables  (#bbe6)
- Explain what it means for each object to have its own copy of instance variables  (#bf9e)
- Identify the scope of local variables  (#ccf8)
- Explain why even within the same class we cannot invoke instance methods from a static method with some variable holding an instance of the class.  (#d09b)
- Write static methods that access static member variables.  (#e9f8)
- Write correct calls to a static method from outside the class.  (#fc34)

## More turtles

# Unit: ArrayLists

## `ArrayList` and its methods

- Write code to declare an ArrayList of type T  (#14a9)
- State the relationship between the size() of an ArrayList and its last index  (#26c7)
- Write code to construct an ArrayList with <>  (#4227)
- Describe the difference between arrays and ArrayLists  (#942b)
- Describe each of the `ArrayList` method on the Java Quick Reference.  (#9480)
- State what happens if you try to use a negative index or one greater than or equal to the size of an `ArrayList` with `get` or `set`  (#ab09)
- State the import statement needed to use ArrayList  (#d229)

## Wrapper classes

- Defining unboxing.  (#10d1)
- State the two places you must use the names of wrapper types  (#2f74)
- Distinguish between primitive values and wrapper types.  (#8282)
- Translate code that uses autoboxing/unboxing to explicit boxing.  (#ebee)

## `ArrayList` traversals

- Write code that uses all the standard loop algorithms with ArrayLists  (#3d15)
- State what happens if you add or remove items to or from an `ArrayList` while iterating over it with an enhanced `for` loop.  (#7740)
- State when you must use a regular for loop with an ArrayList  (#9f70)
- Write code that uses enhanced for loops with ArrayLists  (#cbba)

## `ArrayList` algorithms

- Debug code that incorrectly removes elements from an ArrayList  (#092d)
- Write code to remove elements from an ArrayList  (#423f)
- Write a loop to remove matching elements from ArrayList  (#a423)
- Write code to traverse multiple Strings, arrays, or ArrayLists in parallel  (#a809)
- Write a filtering loop with an ArrayList (copying)  (#eed0)

## Summary and exercises

# Unit: Data from files

## Files

- Write code to close a Scanner.  (#31ef)
- Write code to construct a `File` object.  (#5f91)
- Explain why file operations may throw IOException  (#634c)
- Explain why processing user input line-by-line makes sense.  (#7c3f)
- Write code to read data with File and Scanner  (#7d47)
- Explain why Scanner.nextLine and other next methods interact badly  (#e072)
- Explain the purpose of closing a Scanner.  (#f975)

## Data sets

- Construct tables to represent a dataset  (#2dcf)
- Write code that uses Integer.parseInt  (#34ef)
- Define "dataset"  (#46eb)
- Explain the difference between data in a file and in a program.  (#4e50)
- Write code that uses String.split to extract data  (#6389)
- Write code that uses Double.parseDouble  (#7e2c)
- Write code to produce various summary statistics of a data set  (#c57f)
- Explain the difference between parseInt(s.nextLine()) & s.nextInt()  (#fe42)

# Unit: Algorithms

## Searching Algorithms

- Give examples of O(N) loops and O(N²) loops  (#0594)
- Write an iterative binary search  (#3dde)
- State the condition for using binary search  (#46a4)
- Explain why binary search is more efficient than linear search.  (#ba9b)
- Describe how binary search works  (#bf21)
- Explain what a half-open intervals (is- closed)  (#c6f3)
- Write code to apply a linear search over a 2d array, row by row and then column by column.  (#f638)

## Sorting algorithms

- Write selection sort.  (#7b96)
- Write insertion sort.  (#93ab)
- Demonstrate selection sort with cards  (#9e85)
- Identify sorting algorithm from code (bubble, insertion, selection, merge)  (#aca3)
- Demonstrate insertion sort with cards  (#caea)

## Recursion

- State what mystery recursive methods do.  (#0bf1)
- Define recursion  (#5c48)
- Translate iterative code to recursion  (#8e57)
- Describe a recursive algorithm that is hard to express iteratively  (#9ce0)
- Explain how recursive calls work in terms of the stack and local variables  (#a2df)
- Explain return in terms of the call stack  (#c3ea)
- Write recursive methods over ints, Strings, arrays, and ArrayLists  (#d2c8)

## Recursive searching and sorting

- Demonstrate merge sort with cards  (#1495)
- Describe merge sort.  (#7e6f)
- Write a recursive binary search  (#c3ed)
- Write merge sort  (#d60c)

## Searching and sorting multiple-choice questions

## Easier searching and sorting MCQs

## Medium searching and sorting MCQs

## Hard searching and sorting MCQs

# Unit: Abstraction and program design

## Abstraction

- List three features of Java that provide data abstraction.  (#133b)
- Describe what kind of details a procedural abstraction hides.  (#1667)
- Explain how writing a class creates a data abstraction  (#3974)
- List the mechanisms in Java we've learned for creating abstractions  (#5219)
- Describe a method in terms of its inputs and outputs or side effects  (#5cdc)
- Describe a class in English in terms of its attributes & methods  (#65b1)
- Define "procedural abstraction"  (#69ef)
- Describe a program (e.g. Google docs) abstractly in English  (#9eb1)
- Describe how method decomposition creates layered abstractions  (#ae2e)
- Explain how ints & doubles are an abstraction  (#bf50)
- Explain the role parameters play in a procedural abstraction  (#c2d2)
- Define "abstraction"  (#cb0a)
- Explain how procedural abstraction simplifies code.  (#ea8e)

## Documentation and invariants

- Define a "class invariant"  (#e175)
- Explain how preconditions constrain other code.  (#11b3)
- Define postconditions.  (#1343)
- Describe how postconditions constrain other code.  (#9620)
- Write comments documenting a method's pre- and post-conditions.  (#b306)
- Define preconditions.  (#e803)

## Impact of program design

- Describe ways a dataset can be biased  (#0121)
- Describe ways you give up some privacy when using software  (#1949)
- Describe ways data sets can be incomplete or inaccurate  (#7a4b)
- Discuss ways software can have unintended societal consequences  (#80aa)
- Describe ways a data set can be useful for one question but misleading for another  (#8591)
- Define "system reliability"  (#c20b)
- Define "algorithmic bias"  (#c521)
- Describe ways software or operation failures can compromise privacy  (#eb1d)
- Explain the purpose of an open source license.  (#f08c)

# Unit: AP practice

## MCQ practice

## FRQ practice

# Unit: Free Response Practice

## FRQ 1A - Methods and Control - Part 1

## FRQ 1A - Methods and Control - Part 2

## FRQ 1A - Methods and Control - Part 3

## FRQ 1B - Strings - Part 1

## FRQ 1B - Strings - Part 2

## FRQ 1B - Strings - Part 3

## FRQ 2 - Class Design - Part 1

## FRQ 2 - Class Design - Part 2

## FRQ 2 - Class Design - Part 3

## FRQ3 - ArrayLists - Part 1

## FRQ3 - ArrayLists - Part 2

## FRQ3 - ArrayLists - Part 3

## FRQ4 - 2D Arrays - Part 1

## FRQ4 - 2D Arrays - Part 2

## FRQ4 - 2D Arrays - Part 3

## Retired FRQ - Arrays - ArrayTester - Part A

## Retired FRQ - Arrays - ArrayTester - Part B

## Retired FRQ - Inheritance - NumberGroup - Part B

## Retired FRQ - Inheritance - NumberGroup - Part C

## Pool — not yet placed

- Write bubble sort.  (#4f8f)
- Demonstrate bubble sort with cards  (#51aa)
- Explain the difference between the heap and the stack.  (#7e2a)
- Explain how copyright applies to software  (#c998)
- Explain the difference between a primitive and a reference type in terms of the stack and the heap.  (#fa61)
