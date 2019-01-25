package eu.stamp_project.reneri;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import eu.stamp_project.reneri.instrumentation.StateObserver;
import javassist.*;
import javassist.compiler.MemberResolver;
import org.apache.maven.plugin.MojoExecutionException;
import org.apache.maven.plugin.MojoFailureException;
import org.apache.maven.plugins.annotations.*;
import org.pitest.classinfo.ClassName;
import org.pitest.mutationtest.engine.Location;
import org.pitest.mutationtest.engine.MethodName;
import org.pitest.mutationtest.engine.MutationIdentifier;
import spoon.MavenLauncher;
import spoon.reflect.CtModel;

import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Array;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;


@Mojo(name = "observeMethods", requiresDependencyResolution =  ResolutionScope.TEST)
@Execute(phase = LifecyclePhase.TEST_COMPILE)
public class MethodObservationMojo extends AbstractObservationMojo {

    private final static String PROBE_SNIPPET = "{StateObserver.observeMethodCall(\"%s\", \"%s\", \"%s\", $sig, $args, %s $type, ($w)$_);}";

    @Parameter(property = "methodReport", defaultValue = "${project.build.directory/methods.json}")
    private File methodReport;

    public File getMethodReport() {
        return methodReport;
    }

    public void setMethodReport(File methodReport) {
        this.methodReport = methodReport;
    }

    private List<MethodRecord> illTestedMethods;

    @Override
    public void execute() throws MojoExecutionException, MojoFailureException {
        findTestClasses();

        loadMethodRecords();

        if(noMethodToObserve()) {
            getLog().warn("No method in the report requires observation");
            return;
        }


        installRuntime();

        ensureObservationFolderIsEmpty("methods");

        observeMethods();
    }


    private void findTestClasses() {
        getLog().info("Searching test classes in project");

        MavenLauncher launcher = getLauncherForProject();
        CtModel model = launcher.buildModel();
        setTestClasses(TestClassFinder.findTestClasses(model));
    }


    private void loadMethodRecords() throws MojoExecutionException {
        getLog().info("Loading method records");

        try{
            illTestedMethods = getIllTestedMethodsFromReport();
        }
        catch (IOException exc) {
            throw new MojoExecutionException("Could not read method report file", exc);
        }
    }

    private boolean noMethodToObserve() {
        return illTestedMethods == null || illTestedMethods.isEmpty();
    }


    private List<MethodRecord> getIllTestedMethodsFromReport() throws IOException {
        Gson gson = new Gson();
        FileReader fileReader = new FileReader(methodReport);
        JsonObject document = gson.fromJson(fileReader, JsonObject.class);
        List<MethodRecord> illTestedMethods = new ArrayList<>();

        for (JsonElement methodItem : document.getAsJsonArray("methods")) {
            JsonObject methodJsonObject = methodItem.getAsJsonObject();
            String classification = methodJsonObject.getAsJsonPrimitive("classification").getAsString();

            if (!classification.equals("partially-tested") && !classification.equals("pseudo-tested")) {
                continue;
            }

            MethodRecord methodRecord = new MethodRecord(
                    methodJsonObject.getAsJsonPrimitive("name").getAsString(),
                    methodJsonObject.getAsJsonPrimitive("description").getAsString(),
                    methodJsonObject.getAsJsonPrimitive("class").getAsString(),
                    methodJsonObject.getAsJsonPrimitive("package").getAsString().replace('/', '.')
            );


            ClassName className = ClassName.fromString(methodRecord.getPackage() + "." + methodRecord.getDeclaringClass());
            MethodName methodName = MethodName.fromString(methodRecord.getName());
            Location location = new Location(className, methodName, methodRecord.getDescription());

            for (JsonElement mutationItem : methodJsonObject.getAsJsonArray("mutations")) {
                JsonObject mutationJsonObject = mutationItem.getAsJsonObject();
                String mutationStatus = mutationJsonObject.getAsJsonPrimitive("status").getAsString();
                if (!mutationStatus.equals("SURVIVED")) {
                    continue;
                }
                String mutator = mutationJsonObject.getAsJsonPrimitive("mutator").getAsString();
                methodRecord.getMutations().add(new MutationIdentifier(location, 0, mutator));
            }

            illTestedMethods.add(methodRecord);
        }

        return illTestedMethods;
    }

    private void installRuntime() throws MojoExecutionException {

        getLog().info("Installing classes required for runtime observation");
        installClassInRuntime(StateObserver.class);
        installClassInRuntime(StateObserver.FieldIterator.class);
        installClassInRuntime(javassist.runtime.Desc.class);
    }

    private void installClassInRuntime(Class<?> classToInstall) throws MojoExecutionException {

        getLog().info("Installing " + classToInstall.getTypeName());

        String classResourceName = getResourceName(classToInstall);

        InputStream  classCodeStream = classToInstall.getResourceAsStream(classResourceName);
        if(classCodeStream == null) {
            throw new AssertionError("Could not load class " + classToInstall.getTypeName());
        }
        String[] folders = classToInstall.getPackage().getName().split("\\.");
        Path path = Paths.get(getProject().getBuild().getTestOutputDirectory(), folders);
        try {
            Files.createDirectories(path);
            FileUtils.write(path.resolve(classResourceName), classCodeStream);
        }
        catch (IOException exc) {
            throw new MojoExecutionException("Could not add the state observer class to the test output folder", exc);
        }

    }

    private String getResourceName(Class<?> aClass) {
        return ((aClass.getDeclaringClass() == null)? "" :  aClass.getDeclaringClass().getSimpleName() +  "$") + aClass.getSimpleName() + ".class";

    }

    private void observeMethods() throws MojoExecutionException {

        getLog().info("Observing methods");

        int index = 0;
        for(MethodRecord methodRecord : illTestedMethods) {
            try {
                handleMethod(getPathTo("observations", "methods", Integer.toString(index++)), methodRecord);
            }
            catch (IOException exc) {
                throw new MojoExecutionException("Could not create folder for method " + methodRecord);
            }
        }
    }

    private void handleMethod(Path pathToResults, MethodRecord methodRecord) throws MojoExecutionException {

        getLog().info("Observing method" + methodRecord);

        Path pathToClassFile = getClassFilePath(methodRecord.getClassQualifiedName());
        //TODO: Check if the original buffer is changed or not.

        byte[] originalClassBuffer = readBytes(pathToClassFile);
        byte[] classWithProbe = insertProbeForMethod(methodRecord, originalClassBuffer);
        writeBytes(pathToClassFile, classWithProbe);

        executeTestsOnce(pathToResults.resolve("original"), getInvolvedTestsFor(methodRecord));

        int index = 0;
        for (MutationIdentifier mutation : methodRecord.getMutations()) {

            getLog().info("Observing mutation " + mutation);

            Path mutationObservationResults = pathToResults.resolve(Integer.toString(index++));
            byte[] mutatedClass = mutate(originalClassBuffer, mutation);
            byte[] mutatedClassWithProbe = insertProbeForMethod(methodRecord, mutatedClass);
            writeBytes(pathToClassFile, mutatedClassWithProbe);

            Set<String> testsExecutingMutation = getInvolvedTestsFor(mutation);

            try {
                Files.createDirectories(mutationObservationResults);
            }
            catch (IOException exc) {
                throw new MojoExecutionException("Could not create directories for mutation observation", exc);
            }


            saveMutationInfo(mutationObservationResults, mutation, testsExecutingMutation);
            executeTestsOnce(mutationObservationResults, getInvolvedTestsFor(mutation));
        }

        //Restoring the original class
        writeBytes(pathToClassFile, originalClassBuffer);
    }

    private byte[] insertProbeForMethod(MethodRecord methodRecord, byte[] classBuffer) {
        try {
            ClassPool pool = new ClassPool(null);
            pool.appendSystemPath();
            pool.appendClassPath(new ByteArrayClassPath(methodRecord.getClassQualifiedName(), classBuffer));
            pool.appendClassPath(new ClassClassPath(StateObserver.class));
            pool.importPackage(StateObserver.class.getPackage().getName());

            CtClass classToMutate = pool.getCtClass(methodRecord.getClassQualifiedName());

            if(classToMutate.isFrozen()) {
                classToMutate.defrost(); //??
            }
            CtMethod methodToMutate = classToMutate.getMethod(methodRecord.getName(), methodRecord.getDescription());

            String probe  = String.format(PROBE_SNIPPET,
                    methodRecord.getClassQualifiedName(),
                    methodRecord.getName(),
                    methodRecord.getDescription(),
                    javassist.Modifier.isStatic(methodToMutate.getModifiers()) ? "" : "$class, $0,");

            getLog().debug(probe);

            methodToMutate.insertAfter(probe, true);

            return classToMutate.toBytecode();
        }
        catch (Throwable exc) {
            throw new AssertionError("Inserting the probe should not produce any error.", exc);
        }
    }

    private Set<String> getInvolvedTestsFor(MethodRecord method) throws MojoExecutionException {

        HashSet<String> result = new HashSet<>();

        for (MutationIdentifier mutation : method.getMutations()) {
            result.addAll(getInvolvedTestsFor(mutation));
        }

        return result;

    }





}
