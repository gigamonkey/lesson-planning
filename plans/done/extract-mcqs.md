# Extract MCQ

Please read ced-2025.pdf. On pages 149-160 there are twenty multiple choice
questions. Please extract them and write an XML file `ced-mcqs.xml` where each
question is formatted like this, using <p> for human-readable text in questions
and <code> for code. Remember to wrap code in `CDATA`. If there are questions
that do not fit into exactly this format, you can add elements to mark them up
in some reasonable ways but every question should be in a <mcq> with children
<question> and <answers>.

```xml
<mcq>
  <question>
    <p>Consider the following code segment.</p>
    <code>
      <![CDATA[
      double q = 15.0;
      int r = 2;
      double x = (int) (q / r);
      double y = q / r;
      System.out.println(x + " " + y);
      ]]>
    </code>
    <p>What is printed as a result of executing this code segment?</p>
  </question>
  <answers>
    <item>7.0 7.0</item>
    <item>7.0 7.5</item>
    <item>7.5 7.0</item>
    <item>7.5 7.5</item>
  </answers>
</mcq>
```
