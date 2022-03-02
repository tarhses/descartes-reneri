package eu.stamp_project.reneri;

import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import org.apache.maven.artifact.DependencyResolutionRequiredException;
import org.apache.maven.plugin.MojoExecutionException;
import org.apache.maven.plugin.MojoFailureException;
import org.apache.maven.plugins.annotations.Execute;
import org.apache.maven.plugins.annotations.LifecyclePhase;
import org.apache.maven.plugins.annotations.Mojo;
import org.apache.maven.plugins.annotations.Parameter;
import org.apache.maven.plugins.annotations.ResolutionScope;
import org.eclipse.jdt.core.dom.Modifier;

import eu.stamp_project.reneri.diff.ObservedValueMap;
import eu.stamp_project.reneri.instrumentation.StateObserver;
import eu.stamp_project.reneri.utils.FileUtils;
import javassist.ByteArrayClassPath;
import javassist.CannotCompileException;
import javassist.ClassPool;
import javassist.CtClass;
import javassist.CtMethod;
import javassist.NotFoundException;
import spoon.MavenLauncher;
import spoon.reflect.CtModel;

@Mojo(name = "observeMethods", requiresDependencyResolution = ResolutionScope.TEST)
@Execute(phase = LifecyclePhase.TEST_COMPILE)
public class MethodObservationMojo extends AbstractObservationMojo {

    /**
     * Name of the JSON file in which Descartes's report is stored.
     */
    @Parameter(property = "methodReport", defaultValue = "${project.build.directory}/methods.json")
    private File methodReport;

    public File getMethodReport() {
        return methodReport;
    }

    public void setMethodReport(File methodReport) {
        this.methodReport = methodReport;
    }

    /**
     * Methods that were partially or pseudo tested according to Descartes.
     */
    private List<MethodRecord> illTestedMethods;

    /**
     * Methods that were not covered.
     */
    private List<MethodRecord> uncoveredMethods;
    
    private ClassPool compileClassPool;
    private ClassPool testClassPool;

    @Override
    public void execute() throws MojoExecutionException, MojoFailureException {
        findTestClasses();
        loadMethodRecords();
        writeUncoveredMethods();
        if (noMethodToObserve()) {
            getLog().warn("No method in the report requires observation.");
            return;
        }
        installRuntime();
        ensureObservationFolderIsEmpty("methods");
        observeMethods();
    }

    private void findTestClasses() {
        getLog().info("Searching for test classes in the project");
        MavenLauncher launcher = getLauncherForProject();
        CtModel model = launcher.buildModel();
        setTestClasses(TestClassFinder.findTestClasses(model));
    }

    private void loadMethodRecords() throws MojoExecutionException {
        getLog().info("Loading method records");
        try {
            loadIllTestedMethodsFromReport();
        } catch (IOException exc) {
            throw new MojoExecutionException("Could not read method report file", exc);
        }
    }

    private void loadIllTestedMethodsFromReport() throws IOException, MojoExecutionException {
        Gson gson = new Gson();
        FileReader fileReader = new FileReader(methodReport);
        JsonObject document = gson.fromJson(fileReader, JsonObject.class);

        illTestedMethods = new ArrayList<>();
        uncoveredMethods = new ArrayList<>();

        for (JsonElement methodItem : document.getAsJsonArray("methods")) {
            JsonObject methodJsonObject = methodItem.getAsJsonObject();
            String classification = methodJsonObject.getAsJsonPrimitive("classification").getAsString();
            MethodRecord methodRecord = new MethodRecord(
                    methodJsonObject.getAsJsonPrimitive("name").getAsString(),
                    methodJsonObject.getAsJsonPrimitive("description").getAsString(),
                    methodJsonObject.getAsJsonPrimitive("class").getAsString(),
                    methodJsonObject.getAsJsonPrimitive("package").getAsString().replace('/', '.'));

            if (classification.equals("partially-tested") || classification.equals("pseudo-tested")) {
                for (JsonElement mutationItem : methodJsonObject.getAsJsonArray("mutations")) {
                    JsonObject mutationJsonObject = mutationItem.getAsJsonObject();
                    String mutationStatus = mutationJsonObject.getAsJsonPrimitive("status").getAsString();
                    if (!mutationStatus.equals("SURVIVED")) {
                        continue;
                    }
                    
                    MutationInfo mutationInfo = methodRecord.addMutation(
                        mutationJsonObject.getAsJsonPrimitive("mutator").getAsString(),
                        testClassesFromJsonArray(mutationJsonObject.getAsJsonArray("tests")));
                        complementTests(mutationInfo);
                }

                illTestedMethods.add(methodRecord);
            } else if (classification.equals("not-covered")) {
                uncoveredMethods.add(methodRecord);
            }
        }
    }

    private void writeUncoveredMethods() throws MojoExecutionException {
        List<MethodRecord> methods = getPublicUncoveredMethods();

        Gson gson = new GsonBuilder().create();
        try (FileWriter writer = new FileWriter(getPathTo("observations").resolve("uncovered.json").toFile())) {
            gson.toJson(methods, writer);
        } catch (IOException exc) {
            throw new MojoExecutionException("Could not save uncovered methods", exc);
        }
    }

    private List<MethodRecord> getPublicUncoveredMethods() throws MojoExecutionException {
        List<MethodRecord> result = new ArrayList<>();
        ClassPool pool = getCompileClassPool();
        for (MethodRecord record : uncoveredMethods) {
            try {
                CtMethod method = pool.getMethod(record.getClassQualifiedName(), record.getName());
                if (Modifier.isPublic(method.getModifiers())) {
                    result.add(record);
                }
            } catch (NotFoundException exception) {
                // Ignore
            }
        }
        return result;
    }

    private boolean noMethodToObserve() {
        return illTestedMethods == null || illTestedMethods.isEmpty();
    }

    private void installRuntime() throws MojoExecutionException {
        getLog().info("Installing classes required for runtime observation.");
        installClassInRuntime(StateObserver.class);
        installClassInRuntime(StateObserver.FieldIterator.class);
        installClassInRuntime(javassist.runtime.Desc.class);
    }

    private void installClassInRuntime(Class<?> classToInstall) throws MojoExecutionException {
        getLog().info("Installing " + classToInstall.getTypeName());

        String classResourceName = getResourceName(classToInstall);
        InputStream classCodeStream = classToInstall.getResourceAsStream(classResourceName);
        if (classCodeStream == null) {
            throw new AssertionError("Could not load class " + classToInstall.getTypeName());
        }

        String[] folders = classToInstall.getPackage().getName().split("\\.");
        Path path = Paths.get(getProject().getBuild().getTestOutputDirectory(), folders);
        try {
            Files.createDirectories(path);
            FileUtils.write(path.resolve(classResourceName), classCodeStream);
        } catch (IOException exc) {
            throw new MojoExecutionException("Could not add the state observer class to the test output folder", exc);
        }
    }

    private String getResourceName(Class<?> aClass) {
        Class<?> declaringClass = aClass.getDeclaringClass();
        String declaringClassName = declaringClass == null ? "" : declaringClass.getSimpleName() + "$";
        return declaringClassName + aClass.getSimpleName() + ".class";
    }

    private void observeMethods() throws MojoExecutionException {
        getLog().info("Observing methods.");

        int index = 0;
        for (MethodRecord methodRecord : illTestedMethods) {
            try {
                handleMethod(getPathTo("observations", "methods", Integer.toString(index++)), methodRecord);
            } catch (IOException exc) {
                throw new MojoExecutionException("Could not create folder for method " + methodRecord);
            }
        }
    }

    private void handleMethod(Path pathToResults, MethodRecord methodRecord) throws MojoExecutionException {
        getLog().info("Observing method" + methodRecord);

        // Read the original class
        Path pathToClassFile = getClassFilePath(methodRecord);
        byte[] originalClass = readBytes(pathToClassFile);

        // Instrument the method to observe execution
        byte[] classWithProbe = insertProbeForMethod(methodRecord, originalClass);
        writeBytes(pathToClassFile, classWithProbe);

        // Execute the tests with the original method
        Path originalResults = pathToResults.resolve("original");
        executeTests(originalResults, getTestsToExecute(methodRecord.getMutations()));
        ObservedValueMap originalValues = loadOriginalObservations(pathToResults);

        // Analyze each mutation
        int index = 0;
        for (MutationInfo mutation : methodRecord.getMutations()) {
            Path pathToMutationObservations = pathToResults.resolve(Integer.toString(index++));
            handleMutation(mutation, pathToMutationObservations, methodRecord, originalClass);
            generateDiffReportFor(pathToMutationObservations, originalValues);
        }

        // Restoring the original class
        writeBytes(pathToClassFile, originalClass);
        removeOriginalObservationsIfNeeded(pathToResults);
    }

    private final static String PROBE_SNIPPET = "{eu.stamp_project.reneri.instrumentation.StateObserver.observeMethodCall(\"%s\", \"%s\", \"%s\", $sig, $args, %s $type, ($w)$_);}";

    private byte[] insertProbeForMethod(MethodRecord methodRecord, byte[] classBuffer) throws MojoExecutionException {
        try {
            ClassPool transformationClassPool = new ClassPool(getTestClassPool());
            transformationClassPool.childFirstLookup = true;
            transformationClassPool.appendClassPath(new ByteArrayClassPath(methodRecord.getClassQualifiedName(), classBuffer));

            CtClass classToMutate = transformationClassPool.getCtClass(methodRecord.getClassQualifiedName());
            CtMethod methodToMutate = classToMutate.getMethod(methodRecord.getName(), methodRecord.getDescription());

            String probe = String.format(PROBE_SNIPPET,
                    methodRecord.getClassQualifiedName(),
                    methodRecord.getName(),
                    methodRecord.getDescription(),
                    javassist.Modifier.isStatic(methodToMutate.getModifiers()) ? "" : "$class, $0,");

            getLog().debug(probe);
            methodToMutate.insertAfter(probe, true);
            return classToMutate.toBytecode();
        } catch (CannotCompileException | NotFoundException | IOException exc) {
            throw new AssertionError("Inserting the probe should not produce any error.", exc);
        }
    }

    private void handleMutation(MutationInfo mutation, Path mutationObservationResults, MethodRecord methodRecord, byte[] originalClass) throws MojoExecutionException {
        getLog().info("Observing mutation " + mutation);

        byte[] mutatedClass = mutate(originalClass, mutation.toMutationIdentifier());
        byte[] mutatedClassWithProbe = insertProbeForMethod(methodRecord, mutatedClass);
        writeBytes(getClassFilePath(methodRecord), mutatedClassWithProbe);
        try {
            Files.createDirectories(mutationObservationResults);
        } catch (IOException exc) {
            throw new MojoExecutionException("Could not create directories for mutation observation", exc);
        }

        Set<String> testsExecutingMutation = getTestsToExecute(mutation);
        saveMutationInfo(mutationObservationResults, mutation, testsExecutingMutation);
        executeTests(mutationObservationResults, testsExecutingMutation);
    }

    private ClassPool getCompileClassPool() throws MojoExecutionException {
        if (compileClassPool == null) {
            try {
                compileClassPool = new ClassPool(ClassPool.getDefault());
                for (String path : getProject().getCompileClasspathElements()) {
                    compileClassPool.appendClassPath(path);
                }
            } catch (DependencyResolutionRequiredException exc) {
                throw new MojoExecutionException("Unexpected error while resolving project's classpath", exc);
            } catch (NotFoundException exc) {
                throw new MojoExecutionException("Issues finding project's classpath elements", exc);
            }
        }

        return compileClassPool;
    }

    private ClassPool getTestClassPool() throws MojoExecutionException {
        if (testClassPool == null) {
            try {
                testClassPool = new ClassPool(ClassPool.getDefault());
                for (String path : getProject().getTestClasspathElements()) {
                    testClassPool.appendClassPath(path);
                }
            } catch (DependencyResolutionRequiredException exc) {
                throw new MojoExecutionException("Unexpected error while resolving project's classpath", exc);
            } catch (NotFoundException exc) {
                throw new MojoExecutionException("Issues finding project's classpath elements", exc);
            }
        }

        return testClassPool;
    }
}
