---
title: Computer science guide (HL)
levels: theme, topic, subtopic, learning-statement, content
kind: syllabus
---

# Theme A: Concepts of computer science

## A1 Computer fundamentals (18 hours)

### A1.1 Computer hardware and operation

#### A1.1.1 Describe the functions and interactions of the main CPU components.

##### A1.1.1.1 Units: arithmetic logic unit (ALU), control unit (CU)

##### A1.1.1.2 Registers: instruction register (IR), program counter (PC), memory address register (MAR), memory data register (MDR), accumulator (AC)

##### A1.1.1.3 Buses: address, data, control

##### A1.1.1.4 Processors: single core processor, multi-core processor, co-processors

##### A1.1.1.5 A diagrammatic representation of the relationship between the specified CPU components

#### A1.1.2 Describe the role of a GPU.

##### A1.1.2.1 The architecture that allows graphics processing units (GPUs) to handle specific tasks and makes them suitable for complex computations

##### A1.1.2.2 Real-world scenarios may include video games, artificial intelligence (AI), large simulations and other applications that require graphics rendering and machine learning.

#### A1.1.3 Explain the differences between the CPU and the GPU. (HL only)

##### A1.1.3.1 Differences in their design philosophies, usage scenarios

##### A1.1.3.2 Differences in their core architecture, processing power, memory access, power efficiency

##### A1.1.3.3 CPUs and GPUs working together: task division, data sharing, coordinating execution

#### A1.1.4 Explain the purposes of different types of primary memory.

##### A1.1.4.1 Random-access memory (RAM), read only memory (ROM), cache (L1, L2, L3), registers

##### A1.1.4.2 The interaction of the CPU with different types of memory to optimize performance

##### A1.1.4.3 The relevance of the terms “cache miss” and “cache hit”

#### A1.1.5 Describe the fetch, decode and execute cycle.

##### A1.1.5.1 The basic operations a CPU performs to execute a single instruction in machine language

##### A1.1.5.2 The interaction between memory and registers via the three buses: address, data, control

#### A1.1.6 Describe the process of pipelining in multi-core architectures. (HL only)

##### A1.1.6.1 The instructions fetch, decode, execute

##### A1.1.6.2 Write-back stages to improve the overall system performance in multi-core architectures

##### A1.1.6.3 Overview of how cores in multi-core processors work independently and in parallel

#### A1.1.7 Describe internal and external types of secondary memory storage.

##### A1.1.7.1 Internal hard drives: solid state drive (SSD), hard disk drive (HDD), embedded multimedia cards (eMMCs)

##### A1.1.7.2 External hard drives: SSD, HDD, optical drives, flash drives, memory cards, network attached storage (NAS)

##### A1.1.7.3 The scenarios in which the various types of drive are used

#### A1.1.8 Describe the concept of compression.

##### A1.1.8.1 The differences between lossy compression methods and lossless compression methods

##### A1.1.8.2 Run-length encoding and transform coding

#### A1.1.9 Describe the different types of services in cloud computing.

##### A1.1.9.1 Services: software as a service (SaaS), platform as a service (PaaS), infrastructure as a service (IaaS)

##### A1.1.9.2 The differences between the approaches of SaaS, PaaS, and IaaS in various real-world scenarios, recognizing that different degrees of control and flexibility influence resource management and resource availability

### A1.2 Data representation and computer logic

#### A1.2.1 Describe the principal methods of representing data.

##### A1.2.1.1 The representation of integers in binary and hexadecimal

##### A1.2.1.2 Conversion of binary and hexadecimal integers to decimal, and vice versa

##### A1.2.1.3 Conversion of integers from binary to hexadecimal, and vice versa

#### A1.2.2 Explain how binary is used to store data.

##### A1.2.2.1 The fundamentals of binary encoding and the impact on data storage and retrieval

##### A1.2.2.2 The mechanisms by which data such as integers, strings, characters, images, audio and video are stored in binary form

#### A1.2.3 Describe the purpose and use of logic gates.

##### A1.2.3.1 The purpose and use of logic gates

##### A1.2.3.2 The functions and applications of logic gates in computer systems

##### A1.2.3.3 The role of logic gates in binary computing

##### A1.2.3.4 Boolean operators: AND, OR, NOT, NAND, NOR, XOR, XNOR

#### A1.2.4 Construct and analyse truth tables.

##### A1.2.4.1 Truth tables to predict the output of simple logic circuits

##### A1.2.4.2 Truth tables to determine outputs from inputs for a problem description

##### A1.2.4.3 Truth tables and their relationship to a Boolean expression, with inputs and outputs

##### A1.2.4.4 Truth tables derived from logic diagrams to aid the simplification of logical expressions

##### A1.2.4.5 Karnaugh maps and algebraic simplification to simplify output expressions

#### A1.2.5 Construct logic diagrams.

##### A1.2.5.1 Logic diagrams to demonstrate how logic gates are connected and interact in a circuit.

##### A1.2.5.2 Use of standard gate symbols for AND, OR, NOT, NAND, NOR, XOR and XNOR gates

##### A1.2.5.3 Inputs processed diagrammatically to produce outputs

##### A1.2.5.4 Combinations of these gates to perform more complex logical operations

##### A1.2.5.5 Boolean algebra rules to simplify complex logic diagrams and expressions

### A1.3 Operating systems and control systems

#### A1.3.1 Describe the role of operating systems.

##### A1.3.1.1 Operating systems abstract hardware complexities to manage system resources

#### A1.3.2 Describe the functions of an operating system.

##### A1.3.2.1 Maintaining system integrity while running operating systems’ background operations

##### A1.3.2.2 Memory management, file system, device management, scheduling, security, accounting, graphical user interface (GUI), virtualization, networking

#### A1.3.3 Compare different approaches to scheduling.

##### A1.3.3.1 Managing the execution of processes by allocating CPU time to optimize system performance

##### A1.3.3.2 First-come first-served, round robin, multilevel queue scheduling, priority scheduling

#### A1.3.4 Evaluate the use of polling and interrupt handling.

##### A1.3.4.1 Event frequency, CPU processing overheads, power source (battery or mains), event predictability, controlled latency, security concerns

##### A1.3.4.2 Real-world scenarios may include keyboard and mouse inputs, network communications, disk input/output operations, embedded systems, real-time systems.

#### A1.3.5 Explain the role of the operating system in managing multitasking and resource allocation. (HL only)

##### A1.3.5.1 The challenges of multitasking and resource allocation, including task scheduling, resource contention and deadlock

#### A1.3.6 Describe the use of the control system components. (HL only)

##### A1.3.6.1 The input, process, output, and feedback mechanism (open-loop, closed-loop)

##### A1.3.6.2 Controller, sensors, actuators, transducers and control algorithm

#### A1.3.7 Explain the use of control systems in a range of real-world applications. (HL only)

##### A1.3.7.1 Examples may include autonomous vehicles, home thermostats, automatic elevator controllers, automatic washing machines, traffic signal control systems, irrigation control systems, home security systems, automatic doors.

### A1.4 Translation (HL only)

#### A1.4.1 Evaluate the translation processes of interpreters and compilers.

##### A1.4.1.1 The mechanics and use-cases of each translation approach

##### A1.4.1.2 The difference in error detection, translation time, portability and applicability for different translation processes, including just-in-time compilation (JIT) and bytecode interpreters

##### A1.4.1.3 Example scenarios where the translation method should be considered must include rapid development and testing, performance-critical applications and cross-platform development.

## A2 Networks (18 hours)

### A2.1 Network fundamentals

#### A2.1.1 Describe the purpose and characteristics of networks.

##### A2.1.1.1 Networks: local area network (LAN), wide area network (WAN), personal area network (PAN), virtual private network (VPN)

#### A2.1.2 Describe the purpose, benefits and limitations of modern digital infrastructures.

##### A2.1.2.1 Modern digital infrastructure: the internet, cloud computing, distributed systems, edge computing, mobile networks

##### A2.1.2.2 Examples where specific networks are used may include the worldwide web (WWW), cryptocurrency blockchains, smart traffic lights, a school.

#### A2.1.3 Describe the function of network devices.

##### A2.1.3.1 Gateways, hardware firewalls, modems, network interface cards, routers, switches, wireless access points

##### A2.1.3.2 How devices map to the layers of the TCP/IP model

#### A2.1.4 Describe the network protocols used for transport and application.

##### A2.1.4.1 Protocols: transmission control protocol (TCP), user datagram protocol (UDP), hypertext transfer protocol (HTTP), hypertext transfer protocol secure (HTTPS), dynamic host configuration protocol (DHCP)

#### A2.1.5 Describe the function of the TCP/IP model. (HL only)

##### A2.1.5.1 Application, transport, internet, network interface

##### A2.1.5.2 The role of each layer and the interaction between these layers to ensure reliable data transmission over a network

### A2.2 Network architecture

#### A2.2.1 Describe the functions and practical applications of network topologies.

##### A2.2.1.1 Network topologies: star, mesh, hybrid

##### A2.2.1.2 Factors to consider must include reliability, transmission speed, scalability, data collisions, cost.

##### A2.2.1.3 Examples may include home and small office settings, where reliability is paramount, and the use of networks in larger settings (e.g. corporations, government departments, college campuses).

#### A2.2.2 Describe the function of servers. (HL only)

##### A2.2.2.1 Types of servers: domain name server (DNS), dynamic host configuration protocol (DHCP), file server, mail server, proxy server, web server

##### A2.2.2.2 Factors to consider must include function, scalability, reliability and security.

#### A2.2.3 Compare and contrast networking models.

##### A2.2.3.1 Client-server and peer-to-peer models

##### A2.2.3.2 The respective benefits and drawbacks of client-server and peer-to-peer models

##### A2.2.3.3 Real-world applications may include web browsing, email services, online banking, file sharing, VoIP services, blockchain.

#### A2.2.4 Explain the concepts and applications of network segmentation.

##### A2.2.4.1 Segmentation for network performance and security, to reduce congestion, to manage network resources efficiently

##### A2.2.4.2 Network segmentation must include the uses and roles of segmenting, subnetting and virtual local area networks (VLANs).

### A2.3 Data transmissions

#### A2.3.1 Describe different types of IP addressing.

##### A2.3.1.1 The distinction between IPv4 and IPv6 addressing

##### A2.3.1.2 The differences between public IP addresses and private IP addresses, and between static IP addresses and dynamic IP addresses

##### A2.3.1.3 The role of network address translation (NAT) to minimize the use of IP addresses and to facilitate communication between private internal networks and the public internet

#### A2.3.2 Compare types of media for data transmission.

##### A2.3.2.1 Wired transmission via fibre optic cables and twisted pair cables; wireless transmission

##### A2.3.2.2 The advantages and disadvantages of these three types of data transmission

##### A2.3.2.3 Factors to consider must include bandwidth, complexity of installation, cost, range, susceptibility to interference, attenuation, reliability, security.

#### A2.3.3 Explain how packet switching is used to send data across a network.

##### A2.3.3.1 The process of segmenting data into packets with a routing header attached, and independently transmitting control information, allowing the data to be reassembled at the destination

##### A2.3.3.2 The role that switches and routers play in packet switching

#### A2.3.4 Explain how static routing and dynamic routing move data across local area networks. (HL only)

##### A2.3.4.1 The process of static routing, and its advantages and disadvantages

##### A2.3.4.2 The process of dynamic routing, and its advantages and disadvantages (explanation of a specific routing protocol is not required)

##### A2.3.4.3 Factors to consider must include configuration, maintenance, complexity, resource usage, convergence, scalability, network size.

### A2.4 Network security

#### A2.4.1 Discuss the effectiveness of firewalls at protecting a network.

##### A2.4.1.1 The function of firewalls in inspecting and filtering incoming and outgoing traffic based on whitelists, blacklists and rules

##### A2.4.1.2 The strengths and limitations of firewalls

##### A2.4.1.3 The role of NAT to enhance network security

#### A2.4.2 Describe common network vulnerabilities. (HL only)

##### A2.4.2.1 Distributed denial of service (DDoS), insecure network protocols, malware, man-in-the-middle (MitM) attacks, phishing attacks, SQL injection, cross-site scripting (XSS), unpatched software, weak authentication, zero-day exploits

#### A2.4.3 Describe common network countermeasures. (HL only)

##### A2.4.3.1 Content security policies, complex password policies, DDoS mitigation tools, email filtering solutions, encrypted protocols, input validation (filtering, whitelisting), intrusion detection systems (IDS), intrusion prevention systems (IPS), multifactor authentication (MFA), secure socket layer (SSL) certificate, transport layer security (TLS) certificate, update software, VPNs

##### A2.4.3.2 The importance of regular security testing and employee training

##### A2.4.3.3 Wireless security measures may include media access controllers (MAC), whitelists and blacklists.

#### A2.4.4 Describe the process of encryption and digital certificates.

##### A2.4.4.1 The difference between symmetric and asymmetric cryptography

##### A2.4.4.2 The role of digital certificates in establishing secure network connections

##### A2.4.4.3 The use of public and private keys in asymmetric cryptography

##### A2.4.4.4 The significance of encryption key management

## A3 Databases (18 hours)

### A3.1 Database fundamentals

#### A3.1.1 Explain the features, benefits and limitations of a relational database.

##### A3.1.1.1 Features: composite keys, foreign keys, primary keys, relationships, tables

##### A3.1.1.2 Benefits of databases: community support, concurrency control, data consistency, data integrity, data retrieval, reduced data duplication, reduced redundancy, reliable transaction processing, scalability, security features

##### A3.1.1.3 Limitations of databases: “big data” scalability issues, design complexity, hierarchical data handling, rigid schema, object-relational impedance mismatch, unstructured data handling

### A3.2 Database design

#### A3.2.1 Describe database schemas.

##### A3.2.1.1 Conceptual schema, logical schema, physical schema

##### A3.2.1.2 Abstract definitions of the data structure and organization of the data at different levels

#### A3.2.2 Construct ERDs.

##### A3.2.2.1 The significance of entity relationship diagrams (ERDs) in crafting organized, efficient database designs tailored for specific applications

##### A3.2.2.2 The relationships between different data entities within a database

##### A3.2.2.3 The roles of cardinality and modality in defining relationships in ERDs

#### A3.2.3 Outline the different data types used in relational databases.

##### A3.2.3.1 The importance of data type consistency

##### A3.2.3.2 The potential effects of choosing the wrong data type

#### A3.2.4 Construct tables for relational databases.

##### A3.2.4.1 The relationship between tables using primary keys, foreign keys, composite keys and concatenated keys

##### A3.2.4.2 The importance of well-defined tables in ensuring data integrity

#### A3.2.5 Explain the difference between normal forms.

##### A3.2.5.1 First normal form (1NF), second normal form (2NF), third normal form (3NF)

##### A3.2.5.2 The terms atomicity, unique identification, functional dependencies, partial-key dependencies, non-key/transitive dependencies

##### A3.2.5.3 Normalization issues can encompass data duplication, missing data, and a range of dependency concerns, including data dependencies, composite key dependencies, transitive dependencies, and multi-valued dependencies.

#### A3.2.6 Construct a database normalized to 3NF for a range of real-world scenarios.

##### A3.2.6.1 Examples may include library management, hospital management, e-commerce platforms, school management, employee management, inventory management, police crime reporting

#### A3.2.7 Evaluate the need for denormalizing databases.

##### A3.2.7.1 The advantages and disadvantages of normalizing and denormalizing databases

##### A3.2.7.2 Situations where denormalization can enhance performance, particularly in read-intensive applications

##### A3.2.7.3 The balance between straightforward query structures and the risk of data redundancy in denormalized schemas

### A3.3 Database programming

#### A3.3.1 Outline the differences between data language types within SQL.

##### A3.3.1.1 Data language types must include data definition language (DDL) and data manipulation language (DML)

##### A3.3.1.2 SQL statements to define data structures or to manipulate data

#### A3.3.2 Construct queries between two tables in SQL.

##### A3.3.2.1 Queries must include joins, relational operators, filtering, pattern matching, and ordering data

##### A3.3.2.2 SQL commands: SELECT, DISTINCT, FROM, WHERE, BETWEEN, ORDER BY, GROUP BY, HAVING, ASC, DESC, JOIN, LIKE with % wildcard, AND, OR, NOT (note: Syntax may vary in different database systems)

#### A3.3.3 Explain how SQL can be used to update data in a database.

##### A3.3.3.1 Insert new records (INSERT INTO), modify data (UPDATE SET), remove data (DELETE)

##### A3.3.3.2 The performance implications of updating data in indexed columns, and how indexes might need to be rebuilt or reorganized following significant data modifications

#### A3.3.4 Construct calculations within a database using SQL’s aggregate functions. (HL only)

##### A3.3.4.1 Aggregate functions on grouped data to aid reporting and decision-making

##### A3.3.4.2 Aggregate commands: AVERAGE, COUNT, MAX, MIN, SUM

#### A3.3.5 Describe different database views. (HL only)

##### A3.3.5.1 Virtual views and materialized (snapshot) views

##### A3.3.5.2 Hiding data complexity, data consistency, independence, performance, query simplification, read-only data or updatable data, security

#### A3.3.6 Describe how transactions maintain data integrity in a database. (HL only)

##### A3.3.6.1 The role of atomicity, consistency, isolation and durability (ACID) to ensure reliable processing of transactions

##### A3.3.6.2 Transaction control language (TCL) commands: BEGIN TRANSACTION, COMMIT, ROLLBACK

### A3.4 Alternative databases and data warehouses (HL only)

#### A3.4.1 Outline the different types of databases as approaches to storing data.

##### A3.4.1.1 Databases models: NoSQL, cloud, spatial, in-memory

##### A3.4.1.2 Examples of the use of the database model in real-world scenarios may include e-commerce platforms, geographic information systems (GIS), managed services, real-time analytics, social media platforms, SaaS.

#### A3.4.2 Explain the primary objectives of data warehouses in data management and business intelligence.

##### A3.4.2.1 The roles of append-only data, subject-oriented data, integrated data, time-variant data, non-volatile data and data optimized for query performance, to ensure efficient data storage and analysis

#### A3.4.3 Explain the role of online analytical processing (OLAP) and data mining for business intelligence.

##### A3.4.3.1 Data mining techniques must include classification, clustering, regression, association rule discovery, sequential pattern discovery, anomaly detection (note: This links to “A4 Machine learning”).

##### A3.4.3.2 The uses of the techniques in extracting meaningful information from large data sets

#### A3.4.4 Describe the features of distributed databases.

##### A3.4.4.1 The need to maintain data consistency in a distributed database

##### A3.4.4.2 The role of ACID to ensure reliable processing of transactions in distributed databases

##### A3.4.4.3 Features of distributed databases: concurrency control, data consistency, data partitioning, data security, distribution transparency, fault tolerance, global query processing, location transparency, replication, scalability

## A4 Machine learning (18 hours)

### A4.1 Machine learning fundamentals

#### A4.1.1 Describe the types of machine learning and their applications in the real world.

##### A4.1.1.1 The different approaches to machine learning algorithms and their unique characteristics

##### A4.1.1.2 Deep learning (DL), reinforcement learning (RL), supervised learning, transfer learning (TL), unsupervised learning (UL)

##### A4.1.1.3 Real-world applications of machine learning may include market basket analysis, medical imaging diagnostics, natural language processing, object detection and classification, robotics navigation, sentiment analysis.

#### A4.1.2 Describe the hardware requirements for various scenarios where machine learning is deployed.

##### A4.1.2.1 The hardware configurations for different machine learning scenarios, considering factors such as processing, storage and scalability

##### A4.1.2.2 Hardware configurations for machine learning ranging from standard laptops to advanced infrastructure

##### A4.1.2.3 Advanced infrastructure must include application-specific integrated circuits (ASICs), edge devices, field-programmable gate arrays (FPGAs), GPUs, tensor processing units (TPUs), cloud-based platforms, high-performance computing (HPC) centres.

### A4.2 Data preprocessing (HL only)

#### A4.2.1 Describe the significance of data cleaning.

##### A4.2.1.1 The impact of data quality on model performance

##### A4.2.1.2 Techniques for handling outliers, removing or consolidating duplicate data, identifying incorrect data, filtering irrelevant data, transforming improperly formatted data, and imputation, deletion or predictive modelling for missing data

##### A4.2.1.3 Normalization and standardization as crucial preprocessing steps

#### A4.2.2 Describe the role of feature selection.

##### A4.2.2.1 Feature selection to identify and retain the most informative attributes of the data set

##### A4.2.2.2 Feature selection strategies: filter methods, wrapper methods, embedded methods

#### A4.2.3 Describe the importance of dimensionality reduction.

##### A4.2.3.1 The curse of dimensionality considerations may include overfitting, computational complexity, data sparsity, the effectiveness of distance metrics, data visualization, sample size increases, memory usage.

##### A4.2.3.2 Dimensionality reduction of variables, while preserving the relevant aspects of the data

##### A4.2.3.3 Note: Statistical techniques such as principal component analysis (PCA) and linear discriminant analysis (LDA) are beyond the scope of this course.

### A4.3 Machine learning approaches (HL only)

#### A4.3.1 Explain how linear regression is used to predict continuous outcomes.

##### A4.3.1.1 The relationship between the independent (predictor) and dependent (response) variables

##### A4.3.1.2 The significance of the slope and intercept in the regression equation

##### A4.3.1.3 How well the model fits the data—often assessed using measures like r2.

#### A4.3.2 Explain how classifications techniques in supervised learning are used to predict discrete categorical outcomes.

##### A4.3.2.1 K-Nearest Neighbours (K-NN) and decision trees algorithms to categorize new data points, based on patterns learned from existing labelled data

##### A4.3.2.2 Real-world applications of K-NN may include collaborative filtering recommendation systems.

##### A4.3.2.3 Real-world applications of decision trees may include medical diagnosis based on a patient’s symptoms.

#### A4.3.3 Explain the role of hyperparameter tuning when evaluating supervised learning algorithms.

##### A4.3.3.1 Accuracy, precision, recall and F1 score as evaluation metrics

##### A4.3.3.2 The role of hyperparameter tuning on model performance

##### A4.3.3.3 Overfitting and underfitting when training algorithms

#### A4.3.4 Describe how clustering techniques in unsupervised learning are used to group data based on similarities in features.

##### A4.3.4.1 Clustering techniques in unsupervised learning group data based on feature similarities

##### A4.3.4.2 Real-world applications of clustering may include using purchasing data to segment a customer base.

#### A4.3.5 Describe how learning techniques using the association rule are used to uncover relations between different attributes in large data sets.

##### A4.3.5.1 Mining techniques using the association rule and interpretation of the results for a given scenario For example, in crime analysis, the techniques may reveal that areas with high rates of vandalism also often experience incidents of theft, assisting law enforcement in predictive policing and resource allocation.

#### A4.3.6 Describe how an agent learns to make decisions by interacting with its environment in reinforcement learning.

##### A4.3.6.1 The principle of cumulative reward and the foundational concepts of agent–environment interaction, encompassing actions, states, rewards and policies

##### A4.3.6.2 The exploration versus exploitation trade-off as a core concept in reinforcement learning

#### A4.3.7 Describe the application of genetic algorithms in various real-world situations.

##### A4.3.7.1 For example: population, fitness function, selection, crossover, mutation, evaluation, termination

##### A4.3.7.2 A real-world application of genetic algorithms is seen in optimization problems, such as route planning (e.g. the “travelling salesperson problem”).

#### A4.3.8 Outline the structure and function of ANNs and how multi-layer networks are used to model complex patterns in data sets.

##### A4.3.8.1 An artificial neural network (ANN) to simulate interconnected nodes or “neurons” to process and learn from input data, enabling tasks such as classification, regression and pattern recognition

##### A4.3.8.2 Sketch of a single perceptron, highlighting its input, weights, bias, activation function and output

##### A4.3.8.3 Sketch of a multi-layer perceptron (MLP) encompassing the input layer, one or more hidden layers and the output layer.

#### A4.3.9 Describe how CNNs are designed to adaptively learn spatial hierarchies of features in images.

##### A4.3.9.1 Convolutional neural network (CNN) basic architecture: input layer, convolutional layers, activation functions, pooling layers, fully connected layers, output layer

##### A4.3.9.2 The effect of the number of layers, kernel size and stride, activation function selection, and the loss function on how CNNs process input data and classify images

#### A4.3.10 Explain the importance of model selection and comparison in machine learning.

##### A4.3.10.1 How different algorithms can yield different results depending on the data and type of problem

##### A4.3.10.2 The reasons for selecting specific machine learning models over others, considering factors like the nature of the problem, its complexity and desired outcomes

##### A4.3.10.3 The variability in algorithm performance based on the data’s characteristics

### A4.4 Ethical considerations

#### A4.4.1 Discuss the ethical implications of machine learning in real-world scenarios.

##### A4.4.1.1 Ethical issues may include accountability, algorithmic fairness, bias, consent, environmental impact, privacy, security, societal impact, transparency.

##### A4.4.1.2 The challenges posed by biases in training data

##### A4.4.1.3 The ethics of using machine learning in online communication may include concerns about misinformation, bias, online harassment, anonymity, privacy.

#### A4.4.2 Discuss ethical aspects of the increasing integration of computer technologies into daily life.

##### A4.4.2.1 The importance of continually reassessing ethical guidelines as technology advances

##### A4.4.2.2 The potential implications of emerging technologies such as quantum computing, augmented reality, virtual reality and the pervasive use of AI on society, individual rights, privacy and equity

# Theme B: Computational thinking and problem-solving

## B1 Computational thinking (5 hours)

### B1.1 Approaches to computational thinking

#### B1.1.1 Construct a problem specification.

##### B1.1.1.1 The specification of a problem may include a problem statement, constraints and limitations, objectives and goals, input specifications, output specifications, evaluation criteria.

#### B1.1.2 Describe the fundamental concepts of computational thinking.

##### B1.1.2.1 Abstraction, algorithmic design, decomposition, pattern recognition

#### B1.1.3 Explain how applying computational thinking to fundamental concepts is used to approach and solve problems in computer science.

##### B1.1.3.1 Computational thinking does not necessarily involve programming—it is a toolkit of available techniques for problem-solving.

##### B1.1.3.2 Real-world examples may include software development, data analysis, machine learning, database design, network security.

#### B1.1.4 Trace flowcharts for a range of programming algorithms.

##### B1.1.4.1 Use of standard flowchart symbols to depict processes, decisions and flows of control

##### B1.1.4.2 Standard flowchart symbols: Connector, Decision, Flowline, Input/Output, Process/Operation, Start/End

##### B1.1.4.3 Flowcharts for execution flow, to track changes in variables and to determine output

## B2 Programming (42 hours)

### B2.1 Programming fundamentals

#### B2.1.1 Construct and trace programs using a range of global and local variables of various data types.

##### B2.1.1.1 Data types: Boolean value, char, decimal, integer, string

#### B2.1.2 Construct programs that can extract and manipulate substrings.

##### B2.1.2.1 Writing of programs that accurately identify and extract substrings from given strings, demonstrating the ability to perform various manipulations, such as altering, concatenating or replacing

#### B2.1.3 Describe how programs use common exception handling techniques.

##### B2.1.3.1 Potential points of failure in a program must include unexpected inputs, resource unavailability, logic errors.

##### B2.1.3.2 The role of exception handling in developing programs

##### B2.1.3.3 Exception handling constructs that effectively manage errors must include try/catch in Java, and try/except in Python, along with the finally block.

#### B2.1.4 Construct and use common debugging techniques.

##### B2.1.4.1 Debugging techniques may include trace tables, breakpoint debugging, print statements and step-by-step code execution.

### B2.2 Data structures

#### B2.2.1 Compare static and dynamic data structures.

##### B2.2.1.1 The fundamental differences between static and dynamic data structures, including their underlying mechanisms for memory allocation and resizing

##### B2.2.1.2 The advantages and disadvantages of each type in various scenarios, considering factors such as speed, memory usage, flexibility

#### B2.2.2 Construct programs that apply arrays and Lists.

##### B2.2.2.1 One-dimensional (1D) arrays, two-dimensional (2D) arrays, ArrayLists in Java

##### B2.2.2.2 One-dimensional (1D) Lists and two-dimensional (2D) Lists in Python

##### B2.2.2.3 Add, remove and traverse elements in a dynamic list

#### B2.2.3 Explain the concept of a stack as a “last in, first out” (LIFO) data structure.

##### B2.2.3.1 Must include fundamental operations such as push, pop, peek and isEmpty

##### B2.2.3.2 How stack operations impact both performance and memory usage

##### B2.2.3.3 An appropriate stack for a specific problem

#### B2.2.4 Explain the concept of a queue as a “first in, first out” (FIFO) data structure.

##### B2.2.4.1 Must include fundamental operations such as enqueue, dequeue, front and isEmpty

##### B2.2.4.2 How queue operations impact both performance and memory usage

##### B2.2.4.3 An appropriate queue for a specific problem

### B2.3 Programming constructs

#### B2.3.1 Construct programs that implement the correct sequence of code instructions to meet program objectives.

##### B2.3.1.1 The impact of instruction order on program functionality

##### B2.3.1.2 Ways to avoid errors, such as infinite loops, deadlock, incorrect output

#### B2.3.2 Construct programs utilizing appropriate selection structures.

##### B2.3.2.1 Must include: if, else, else if (Java), elif (Python), to execute different code blocks based on specified conditions

##### B2.3.2.2 Selection structures with or without Boolean operators (AND, OR, NOT) and/or relational operators (<, <=, >, >=, ==, !=) to control program flow effectively

#### B2.3.3 Construct programs that utilize looping structures to perform repeated actions.

##### B2.3.3.1 Types of loops, including counted loops and conditional loops, and appropriate use of each type

##### B2.3.3.2 Conditional statements within loops, using Boolean and/or relational operators to govern the loop’s execution

#### B2.3.4 Construct functions and modularization.

##### B2.3.4.1 Functions to define reusable blocks of code with different inputs

##### B2.3.4.2 Modularization to create well-structured, reusable and maintainable code

##### B2.3.4.3 The principles of scope (local versus global)

##### B2.3.4.4 The benefits of code modularization, applying this concept to various programming scenarios

### B2.4 Programming algorithms

#### B2.4.1 Describe the efficiency of specific algorithms by calculating their Big O notation to analyse their scalability.

##### B2.4.1.1 The time and space complexities of algorithms and calculating Big O notation

##### B2.4.1.2 Algorithm choice based on scalability and efficiency requirements

#### B2.4.2 Construct and trace algorithms to implement a linear search and a binary search for data retrieval.

##### B2.4.2.1 The differences in efficiency between different methods of linear and binary search

##### B2.4.2.2 Use of search technique based on efficiency requirements—for example, searching a database for a sorted/indexed list of names to find a phone number, versus searching by the number to identify the name

#### B2.4.3 Construct and trace algorithms to implement bubble sort and selection sort, evaluating their time and space complexities.

##### B2.4.3.1 The time and space complexities of each algorithm, denoted by their respective Big O notations

##### B2.4.3.2 The advantages and disadvantages of each algorithm in terms of efficiency across various data sets

#### B2.4.4 Explain the fundamental concept of recursion and its applications in programming. (HL only)

##### B2.4.4.1 The fundamentals of recursion and its advantages and limitations

##### B2.4.4.2 The utility of recursion in solving problems that can be broken down into smaller, similar sub-problems

##### B2.4.4.3 Recursive algorithms, including but not limited to quicksort

##### B2.4.4.4 The limitations of recursion, including complexity and memory usage

##### B2.4.4.5 Situations that best suit the use of recursion, including fractal image creation, traversing binary trees, sorting algorithms

#### B2.4.5 Construct and trace recursive algorithms in a programming language. (HL only)

##### B2.4.5.1 Simple, non-branching recursive algorithms in programming only

### B2.5 File processing

#### B2.5.1 Construct code to perform file-processing operations.

##### B2.5.1.1 Programs that manipulate text files

##### B2.5.1.2 Opening a sequential file in various modes (read, write, append)

##### B2.5.1.3 How to read from and write to files, append data to an existing file, and close a file once operations are completed

##### B2.5.1.4 Classes for Java users may include Scanner, FileWriter, BufferedReader.

##### B2.5.1.5 Functions for Python users may include open(), read(), readline(), write(), close().

## B3 Object-oriented programming (23 hours)

### B3.1 Fundamentals of OOP for a single class

#### B3.1.1 Evaluate the fundamentals of OOP.

##### B3.1.1.1 Model real-world entities using OOP concepts: classes, objects, inheritance, encapsulation, polymorphism

##### B3.1.1.2 The advantages and disadvantages of using OOP in various programming scenarios

#### B3.1.2 Construct a design of classes, their methods and behaviour.

##### B3.1.2.1 Classes and their methods, based on application requirements

##### B3.1.2.2 The use of unified modelling language (UML) class diagrams to represent class relationships, attributes and methods, to aid effective software design and planning

#### B3.1.3 Distinguish between static and non-static variables and methods.

##### B3.1.3.1 The differences between static and non-static variables and methods, including their usage and scope

##### B3.1.3.2 When to use instance variables instead of class variables, and how to apply these concepts effectively in code

#### B3.1.4 Construct code to define classes and instantiate objects.

##### B3.1.4.1 How to define classes and create objects from those classes

##### B3.1.4.2 The role of constructors in initializing an object's state, setting initial values for its attributes to define its condition or characteristics at the time of creation

#### B3.1.5 Explain and apply the concepts of encapsulation and information hiding in OOP.

##### B3.1.5.1 The principles of encapsulation and information hiding

##### B3.1.5.2 Apply access modifiers such as private and public

##### B3.1.5.3 Controlling access to class members

##### B3.1.5.4 The importance of limiting access to maintain the integrity and security of an object's state

### B3.2 Fundamentals of OOP for multiple classes (HL only)

#### B3.2.1 Explain and apply the concept of inheritance in OOP to promote code reusability.

##### B3.2.1.1 How inheritance enables a hierarchical relationship between parent and child classes

##### B3.2.1.2 Extending existing classes, utilizing inheritance to reuse and extend functionalities

##### B3.2.1.3 The impact of inheritance on access to parent class members with different access modifiers (private, public, protected, default)

#### B3.2.2 Construct code to model polymorphism and its various forms, such as method overriding.

##### B3.2.2.1 The principle of polymorphism and how it contributes to code flexibility and reusability

##### B3.2.2.2 How to implement dynamic polymorphic behaviour through mechanisms like method overriding

##### B3.2.2.3 How to apply static polymorphic behaviour to maximize code efficiency

#### B3.2.3 Explain the concept of abstraction in OOP.

##### B3.2.3.1 The significance of abstraction in the development of modular code fragments

##### B3.2.3.2 The use of abstract classes to establish common interfaces for sub-classes

#### B3.2.4 Explain the role of composition and aggregation in class relationships.

##### B3.2.4.1 How to design objects by leveraging smaller component objects through composition and aggregation

##### B3.2.4.2 That aggregation implies that the subcomponents can function independently of the aggregating class, while in composition, the subcomponents are tightly coupled and cannot exist outside the aggregating class

#### B3.2.5 Explain commonly used design patterns in OOP.

##### B3.2.5.1 The key design patterns such as singleton, factory and observer

##### B3.2.5.2 The application of design patterns in solving recurring programming challenges

## B4 Abstract data types (HL only) (23 hours)

### B4.1 Fundamentals of ADTs

#### B4.1.1 Explain the properties and purpose of ADTs in programming.

##### B4.1.1.1 The core principles of ADTs, including their purpose in providing a high-level description of data structures and their associated operations

#### B4.1.2 Evaluate linked lists.

##### B4.1.2.1 Lists must include singly, doubly, circular

##### B4.1.2.2 Sketch of linked lists and implementation of basic operations diagrammatically, such as insertion, deletion, traversal, search

##### B4.1.2.3 The advantages and disadvantages of using linked lists over other data structures like arrays, particularly in terms of memory utilization and performance

#### B4.1.3 Construct and apply linked lists: singly, doubly and circular.

##### B4.1.3.1 The basic operations on a linked list, such as insertion, deletion, traversal, search

#### B4.1.4 Explain the structures and properties of BSTs.

##### B4.1.4.1 How binary search trees (BSTs) are used for data organization

##### B4.1.4.2 Insert, delete, traverse and searching nodes in a BST

##### B4.1.4.3 Sketching a BST as a tree diagram

#### B4.1.5 Construct and apply sets as an ADT.

##### B4.1.5.1 The fundamental characteristics of sets, including their unordered nature and the uniqueness of elements

##### B4.1.5.2 Operations: union, intersection and difference

##### B4.1.5.3 Code to check if an element is in a set, to add an element to a set, to remove an element, and to check whether one set is a subset/superset of another set

#### B4.1.6 Explain the core principles of ADTs.

##### B4.1.6.1 High-level description of data structures and their associated operations and purpose

##### B4.1.6.2 The underlying mechanics of hash tables, including hashing functions, collision resolution strategies and load factors

##### B4.1.6.3 The underlying mechanics of sets to store and manage data

##### B4.1.6.4 HashMap and HashSet in Java; dict and set in Python
